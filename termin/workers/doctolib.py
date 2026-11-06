"""Doctolib polling, recovery, and alert scheduling."""

import os
import random
import shutil
import subprocess
import time
import traceback
from datetime import datetime

from termin.alerts import notify, play_sound
from termin.diagnostics import log_debug
from termin.ledger import _dl_summarize, _fmt_date, dl_apply_poll, filter_dl_slots
from termin.paths import DL_PROFILE_DIR, SOURCE_FILE, SRC_MTIME, UA
from termin.providers.doctolib import dl_check, dl_load
from termin.storage import (
    _save_cfg_patch,
    load_config,
    load_ledger,
    save_doctolib,
    save_ledger,
)
from termin.terminal import BLUE, BOLD, GREEN, RED, YELLOW


def doctolib_worker(model, args, on_update):
    """Doctolib counterpart of monitor_worker: one browser page is kept for
    both discovery and the light JSON polls."""
    from playwright.sync_api import sync_playwright, Error as PlaywrightError

    ledger = load_ledger()
    try:
        with sync_playwright() as p:
            ctx = page = None
            fail_streak = 0
            cycle_end = None
            try:
                while not model.stopping:
                    if time.time() < model.next_at:
                        time.sleep(0.2)
                        continue
                    model.checking = True
                    model.cycle += 1
                    # Measure lateness against the schedule, excluding intentional backoff.
                    late = time.time() - model.next_at if model.next_at else 0
                    if late > 120:
                        log_debug(f"check started {int(late) // 60} min late (system sleep?)")
                    if ctx is not None and cycle_end and time.time() - cycle_end > 900:
                        # Recycle after idle to recover stalled Chromium DNS resolution.
                        log_debug("browser idle >15 min — recycling before the check")
                        try:
                            ctx.close()
                        except Exception:
                            pass
                        ctx = page = None
                    if not getattr(model, "stale", False) and os.path.getmtime(SOURCE_FILE) != SRC_MTIME:
                        model.stale = True
                        model.event("code updated on disk — restart to apply", YELLOW)
                        log_debug("running build is stale — source file changed on disk")
                    backoff = False
                    ok_count = fail_count = 0
                    try:
                        if ctx is None:
                            try:
                                ctx = p.chromium.launch_persistent_context(
                                    str(DL_PROFILE_DIR), channel="chrome",
                                    headless=True, locale="de-DE", user_agent=UA)
                            except PlaywrightError as e:
                                if "ProcessSingleton" not in str(e) and "SingletonLock" not in str(e):
                                    raise
                                log_debug("browser profile in use (another monitor "
                                          "running?) — using a temporary session")
                                model.event("profile in use — is another monitor "
                                            "running? using temporary session", YELLOW)
                                ctx = p.chromium.launch(
                                    channel="chrome", headless=True).new_context(
                                    locale="de-DE", user_agent=UA)
                            page = None
                        if page is None or page.is_closed():
                            page = ctx.pages[0] if ctx.pages else ctx.new_page()

                        for w in list(model.offices):
                            if model.stopping:
                                break
                            st = model.state.get(w["name"])
                            if st is None:
                                continue
                            if w.get("paused"):
                                st["status"] = "paused"
                                continue
                            st["status"] = "checking"
                            on_update()
                            try:
                                slots, nxt, feed, fail_reason = dl_check(page, w)
                            except PlaywrightError as e:
                                st["status"] = "error"
                                st["reason"] = "network/browser"
                                st.setdefault("fail_since", datetime.now().strftime("%H:%M"))
                                fail_count += 1
                                backoff = True
                                model.event(f"{w['name']}: browser error, retrying "
                                            f"({str(e).splitlines()[0][:60]})", YELLOW)
                                log_debug(f"{w['name']}: PlaywrightError {str(e).splitlines()[0][:120]}")
                                try:
                                    ctx.close()
                                except Exception:
                                    pass
                                ctx = page = None
                                break

                            st["checked"] = datetime.now().strftime("%H:%M:%S")
                            if slots is not None:
                                start, end = getattr(args, "between", None) or (None, None)
                                slots = filter_dl_slots(
                                    slots, start, end, getattr(args, "days", None))
                            if slots is None:
                                st["status"] = "error"
                                st["reason"] = fail_reason
                                st.setdefault("fail_since", datetime.now().strftime("%H:%M"))
                                backoff = True
                                fail_count += 1
                                model.event(f"{w['name']}: {fail_reason} — "
                                            f"backing off (press l for logs)", YELLOW)
                                on_update()
                                continue
                            was_failing = st.pop("reason", None) is not None
                            st.pop("fail_since", None)
                            ok_count += 1
                            if was_failing:
                                model.event(f"✓ {w['name']}: recovered", GREEN)
                                log_debug(f"{w['name']}: recovered")
                            changed = feed != w.get("feed") or not w.get("seeded")
                            w["feed"] = feed
                            now_ts = time.time()
                            led = ledger.setdefault(w["book"], {})
                            new, new_days = dl_apply_poll(w, led, slots, now_ts)
                            save_ledger(ledger)
                            if changed:
                                save_doctolib(model.offices)
                            _dl_summarize(st, led, nxt, now_ts)

                            if new_days:
                                days_txt = ", ".join(_fmt_date(d) for d in new_days)
                                model.event(f"🚨 {w['name']}: NEW day {days_txt}",
                                            BOLD + GREEN)
                                notify(f"Doctolib: {w['name']}",
                                       f"New bookable day: {days_txt}")
                                if args.open:
                                    subprocess.run(["open", w["book"]], check=False)
                                on_update()
                                play_sound(args.sound, times=3)
                            elif new:
                                model.event(f"{w['name']}: +{len(new)} new time(s) "
                                            f"on existing days", BLUE)
                            on_update()
                    except Exception as e:
                        model.event(f"unexpected error: {e!r} — continuing", RED)
                        log_debug("cycle error:\n" + traceback.format_exc())
                        on_update()

                    delay = args.interval + random.randint(0, 30)
                    if backoff:
                        fail_streak += 1
                        delay *= min(2 ** fail_streak, 8)
                    else:
                        fail_streak = 0

                    # Compare with a fresh profile before resetting persistent state.
                    # Limit resets to once per day to avoid repeated identity changes.
                    if fail_streak > 0 and fail_streak % 3 == 0 and not model.stopping:
                        log_debug(f"{fail_streak} failed cycles — probing with a fresh identity")
                        fresh_ok = False
                        try:
                            fb = p.chromium.launch(channel="chrome", headless=True)
                            fpage = fb.new_context(locale="de-DE", user_agent=UA).new_page()
                            _f, fbodies, _r = dl_load(fpage, model.offices[0]["book"])
                            fresh_ok = bool(fbodies)
                            fb.close()
                        except Exception as e:
                            log_debug(f"fresh-identity probe failed: {e!r}")
                        recently_wiped = (time.time() - (load_config() or {}).get(
                            "dl_last_wipe", 0)) < 86400
                        if fresh_ok and not recently_wiped:
                            model.event("browser profile flagged — resetting identity", YELLOW)
                            log_debug("fresh identity works while profile fails — wiping .dl_profile")
                            try:
                                ctx.close()
                            except Exception:
                                pass
                            ctx = page = None
                            shutil.rmtree(DL_PROFILE_DIR, ignore_errors=True)
                            _save_cfg_patch(dl_last_wipe=time.time())
                            fail_streak = 0
                            delay = 5
                        else:
                            if fresh_ok:
                                log_debug("profile already wiped in the last 24h — "
                                          "treating as IP-level instead of wiping again")
                            else:
                                log_debug("fresh identity also blocked — IP-level")
                            model.event("IP-level block — cooling down ~2h", YELLOW)
                            delay = 7200 + random.randint(0, 600)
                            log_debug(f"cooling down — next doctolib check in ~{delay // 60} min")

                    if 0 <= datetime.now().hour < 7 and delay < 3600:
                        delay = 3600 + random.randint(0, 300)
                        log_debug("quiet hours (00–07) — next check in ~1h")

                    log_debug(f"cycle {model.cycle}: ok={ok_count} fail={fail_count} "
                              f"streak={fail_streak} next in ~{delay // 60}min")
                    model.next_at = 0 if model.check_asap else time.time() + delay
                    model.check_asap = False
                    model.checking = False
                    cycle_end = time.time()
                    if args.once:
                        break
            finally:
                model.checking = False
                try:
                    if ctx is not None:
                        ctx.close()
                except Exception:
                    pass
    except Exception:
        pass  # Ignore Playwright teardown errors after interruption.
