"""Watch Berlin (service.berlin.de) appointment calendars and chime when a NEW bookable day appears.

Pick ONE appointment type and ANY NUMBER of offices. Offices are discovered
live from service.berlin.de, so any Dienstleistung ID or URL works, not just
the built-in catalog. Your last selection is remembered and offered as the
default on the next start.

The booking calendars sit behind a JavaScript bot-check, so plain HTTP
requests get a 403. This script drives your installed Google Chrome
headlessly via Playwright, which passes the check like a normal browser.

Setup (one time):
    pip3 install playwright        # uses your installed Chrome, no download

Examples:
    python3 termin_monitor.py                          # live dashboard, last selection
    python3 termin_monitor.py -s citizenship-test -o mitte treptow
    python3 termin_monitor.py -s register-address -o all --interval 120
    python3 termin_monitor.py -s 121151 --list-offices # any service.berlin.de ID works"""

import argparse
import sys
from datetime import datetime

from termin.alerts import DEFAULT_SOUND
from termin.app import _farewell, berlin_flow, doctolib_flow, run_headless, run_live
from termin.diagnostics import log_debug
from termin.model import Model
from termin.paths import SRC_MTIME
from termin.providers.berlin import KNOWN_SERVICES
from termin.storage import load_config, save_mode
from termin.terminal import IS_TTY
from termin.ui.setup import configure, mode_select, welcome_screen
from termin.workers.doctolib import doctolib_worker


def main():
    ap = argparse.ArgumentParser(
        description=__doc__.strip().split("\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Running without flags goes straight to the live dashboard with "
               "your last selection; configure everything from there with hotkeys.")
    ap.add_argument("-s", "--service",
                    help="appointment type (one): alias from --list-services, "
                         "a numeric Dienstleistung ID, or its service.berlin.de URL")
    ap.add_argument("-o", "--offices", nargs="+", metavar="OFFICE",
                    help='offices to watch (many): district/name substrings, or "all"')
    ap.add_argument("--list-services", action="store_true",
                    help="show the built-in service catalog and exit")
    ap.add_argument("--list-offices", action="store_true",
                    help="show all offices for the chosen service and exit")
    ap.add_argument("--setup", action="store_true",
                    help="start from a blank setup and refresh office data")
    ap.add_argument("--interval", type=int, default=240,
                    help="seconds between checks (default: 240)")
    ap.add_argument("--once", action="store_true",
                    help="run a single check and exit")
    ap.add_argument("--open", action="store_true",
                    help="auto-open the booking page in your browser when a slot appears")
    ap.add_argument("--sound", default=DEFAULT_SOUND,
                    help=f"alert sound file (default: {DEFAULT_SOUND})")
    ap.add_argument("--between", default=None, metavar="HH:MM-HH:MM", type=_time_window,
                    help="Doctolib only: only count slots starting in this time window, "
                         "e.g. 17:00-20:00")
    ap.add_argument("--days", type=int, default=None,
                    help="Doctolib only: ignore slots further than N days ahead")
    args = ap.parse_args()

    if args.list_services:
        for alias, (sid, label) in KNOWN_SERVICES.items():
            print(f"  {alias:28s} {label}  (ID {sid})")
        print("\n  …or pass any service.berlin.de Dienstleistung ID / URL.")
        return

    log_debug(f"monitor started (build {datetime.fromtimestamp(SRC_MTIME):%d.%m %H:%M})")

    interactive = IS_TTY and sys.stdin.isatty() and not args.once

    if args.service or args.offices or args.list_offices:
        service_id, service_name, offices = configure(args)
        model = Model(service_id, service_name, offices, args.interval)
        if not interactive:
            run_headless(args, model)
            return
        while run_live(args, model) != "quit":
            if welcome_screen(model, args) != "start":
                break
        _farewell(model)
        return

    if not interactive:
        config = load_config() or {}
        if config.get("mode") == "doctolib" and config.get("doctolib"):
            model = Model(None, "Doctolib doctors", config["doctolib"],
                          args.interval, mode="doctolib")
            run_headless(args, model, doctolib_worker)
        else:
            service_id, service_name, offices = configure(args)
            run_headless(args, Model(service_id, service_name, offices, args.interval))
        return

    while True:
        config = load_config() or {}
        mode = mode_select(config.get("mode", "berlin"))
        if mode is None:
            print("\n  bye 👋")
            return
        save_mode(mode)
        flow = berlin_flow if mode == "berlin" else doctolib_flow
        if flow(args) == "quit":
            return


def _time_window(text):
    """Parse 'HH:MM-HH:MM' into ('HH:MM', 'HH:MM')."""
    try:
        start, end = (datetime.strptime(t.strip(), "%H:%M") for t in text.split("-"))
    except ValueError:
        raise argparse.ArgumentTypeError("expected HH:MM-HH:MM, e.g. 17:00-20:00")
    if end < start:
        raise argparse.ArgumentTypeError("window end is before its start")
    return start.strftime("%H:%M"), end.strftime("%H:%M")


def run():
    """Run the CLI with the original interrupt and terminal cleanup behavior."""
    try:
        main()
    except KeyboardInterrupt:
        if IS_TTY:
            sys.stdout.write("\033[?25h")
        print("\n\n  bye 👋")
        sys.exit(0)
