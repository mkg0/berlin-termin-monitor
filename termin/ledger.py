"""Doctolib availability transitions and row summaries, independent of browser I/O."""

from datetime import datetime, timedelta


DL_NEW_SECS = 3600  # Highlight new slots for one hour.


DL_GONE_SECS = 5 * 3600  # Retain taken slots for five hours.


def filter_dl_slots(slots, from_time=None, to_time=None, days=None, today=None):
    """Keep slot keys ('YYYY-MM-DDTHH:MM') inside the time-of-day window and
    within `days` days from today. Unset limits are not applied."""
    today = today or datetime.now().date()
    last_day = (today + timedelta(days=days)).isoformat() if days is not None else None
    out = []
    for k in slots:
        if last_day is not None and k[:10] > last_day:
            continue
        hm = k[11:16]
        if from_time and hm < from_time:
            continue
        if to_time and hm > to_time:
            continue
        out.append(k)
    return out


def update_dl_ledger(led, slots, now):
    """Fold this poll into the watch's ledger. Returns (new_keys, new_days).

    Alerting follows the Berlin rule: a DAY that previously had nothing
    bookable chimes; new times on an already-bookable day stay silent."""
    current = set(slots)
    prev_active = {k for k, v in led.items() if not v.get("gone")}
    prev_days = {k[:10] for k in prev_active}
    new = sorted(current - prev_active)
    for k in new:
        led[k] = {"first": now, "gone": None}
    for k in prev_active - current:
        led[k]["gone"] = now
    today = datetime.now().strftime("%Y-%m-%d")
    for k in list(led):
        gone = led[k].get("gone")
        if k[:10] < today or (gone and now - gone > DL_GONE_SECS):
            del led[k]
    new_days = sorted({k[:10] for k in new} - prev_days)
    return new, new_days


def dl_apply_poll(watch, led, slots, now):
    """Fold a poll into the ledger. A watch's first successful poll is the
    baseline: nothing is 'new', nothing chimes."""
    new, new_days = update_dl_ledger(led, slots, now)
    if not watch.get("seeded"):
        for k in new:
            led[k]["first"] = now - DL_NEW_SECS
        new, new_days = [], []
        watch["seeded"] = True
    return new, new_days


def _fmt_slot(key):        # '2026-10-20T11:30' -> '20.10. 11:30'
    return f"{key[8:10]}.{key[5:7]}. {key[11:16]}"


def _fmt_date(date_str):   # '2026-11-12' -> '12.11.2026'
    return f"{date_str[8:10]}.{date_str[5:7]}.{date_str[:4]}"


def _dl_summarize(st, led, next_slot, now):
    """Set the row's summary fields from the ledger snapshot."""
    active = [k for k, v in led.items() if not v.get("gone")]
    fresh = [k for k in active if now - led[k].get("first", 0) < DL_NEW_SECS]
    st["ledger"] = {k: dict(v) for k, v in led.items()}
    st["next"] = _fmt_date(next_slot) if next_slot else None
    if active:
        st["status"] = "available"
        st["dates"] = [_fmt_date(d) for d in sorted({k[:10] for k in active})]
        st["summary"] = (f"{len({k[:10] for k in active})} days · {len(active)} slots"
                         + (f" · {len(fresh)} new" if fresh else ""))
    else:
        st["status"] = "none"
        st["dates"] = []
        st["summary"] = ""
