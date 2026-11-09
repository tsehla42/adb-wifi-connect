import argparse
import logging
import queue
import sys
import time

from . import adb
from .env import load_env
from .flow import (connect, connect_single_device, start_pairing_session,
                   direct_connect, enrich_devices)
from .mdns import discover_all_phones
from .ui import (select_devices, _print_device_list, format_label, _status,
                 poll_stdin)
from .watch import watch_loop

log = logging.getLogger(__name__)

_CLI_EPILOG = """examples:
  awc                                    instant QR + auto-discovery + connect
  awc --direct                           guided: prompts for IP, pairs only if needed
  awc --direct 8.8.8.8:5555              connect an already-paired device (explicit port)
  awc --direct 8.8.8.8:37000 123456      pair with code (port = pairing port), then connect
  awc --direct 10.0.0.5:37000 123456 --port 41234   remote: both ports explicit
  awc --watch --direct 8.8.8.8:5555      connect, then keep reconnecting
note: --direct takes its values last — put other flags before it.
"""


def _guarded(fn):
    try:
        return fn()
    except KeyboardInterrupt:
        print("\n\n  Cancelled.")
        sys.exit(1)


def _drain_scan_events(session):
    """One bounded drain pass over the session's event queue (get_nowait
    until Empty): every 'scanned' payload comes back in order; each
    'scan-failed' prints its retry line right here, once per event.
    No session -> nothing to drain."""
    scanned = []
    if session is None:
        return scanned
    while True:
        try:
            event = session.events.get_nowait()
        except queue.Empty:
            return scanned
        kind = event.get("kind")
        if kind == "scanned":
            scanned.append(event)
        elif kind == "scan-failed":
            print(f"  Pairing failed for {event.get('ip')} — scan again")


def _verify_scanned(event):
    """Confirm a queued scan has a LIVE transport before anything is
    announced — the listener's connect is best-effort. Echo-test
    (is_device_connected also reconnects stale entries); if it does not
    confirm, retry the connect once and re-verify."""
    if adb.is_device_connected(event.get("ip")):
        return True
    port = event.get("port")
    if port is None:
        return False
    adb.run_adb_connect(event.get("ip"), port, echo=False)
    return bool(adb.is_device_connected(event.get("ip")))


def _finish_scanned(event, on_connect, announce):
    """Terminal handling of one scanned event at a WAITING site.
    `announce` is the exact success line. Returns the target only on a
    CONFIRMED transport; a dead one prints the retry line and returns
    None so the caller keeps waiting (pairing already succeeded — the
    still-running listener serves the rescan)."""
    if not _verify_scanned(event):
        ip = event.get("ip")
        print(f"  Scan paired {ip} but connect failed — scan again or "
              f"check Wireless debugging")
        return None
    target = event.get("target")
    print(announce.format(target=target))
    adb.run_on_connect(on_connect, target)
    return target


def _early_scan_target(session, on_connect):
    """Drain-before-selection: a queued scan has ALREADY asked to be the
    target — verified and announced, so the run ends before any
    selection UI could appear for the other phones. Unverifiable scans
    fall through to normal routing (the session stays alive to serve
    the rescan)."""
    for event in _drain_scan_events(session):
        target = _finish_scanned(event, on_connect, "\nConnected: {target}")
        if target:
            return target
    return None


def _absorb_scanned(session, devices, selected):
    """Append a record for every queued scanned event — straight from
    the payload — and force-check its index, so the connect loop serves
    it like any other selected phone (flow's already-connected
    short-circuit gives it a single status line)."""
    for event in _drain_scan_events(session):
        devices.append((event.get("ip"), event.get("short"), True, True,
                        event.get("port"), event.get("identity")))
        selected.append(len(devices) - 1)


def _record_paired(rec):
    """paired flag of a discovery record (legacy 3-tuples carry none)."""
    return bool(rec[3]) if len(rec) > 3 else False


def _record_port(rec):
    """Connection-port slot of a discovery record (3-tuples: None)."""
    return rec[4] if len(rec) > 4 else None


def _discard_stdin_line():
    """Consume one pending stdin line so a stray Enter can neither spin
    a wait loop nor leak into a later prompt. Returns False when there
    was nothing left to consume — EOF (closed pipe, `< /dev/null`,
    exhausted redirect) or an unreadable stream — so the caller stops
    polling a stdin that reports ready FOREVER and paces its drains
    with sleep instead (no busy-spin); True when a line was consumed
    (live stdin). KeyboardInterrupt is never swallowed."""
    try:
        return sys.stdin.readline() != ""
    except (OSError, ValueError):
        return False


def _drain_one_scan(session, on_connect):
    """One wait-loop iteration at a VERIFIED-scan waiting site: drain
    the event queue and hand the first confirming 'scanned' payload to
    `_finish_scanned` (announce line + hook). Returns the target, or
    None when nothing has scanned yet — or the scan would not confirm,
    so the caller keeps waiting (the retry line was already printed)."""
    for event in _drain_scan_events(session):
        target = _finish_scanned(event, on_connect, "  Connected: {target}")
        if target:
            return target
    return None


def main():
    load_env()
    parser = argparse.ArgumentParser(
        description="ADB WiFi Connection Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_CLI_EPILOG)
    parser.add_argument("--ip", default=None, help="Device IP address (auto-discovered if omitted)")
    parser.add_argument("--port", type=int, help="Connection port (skip prompt)")
    parser.add_argument("--direct", nargs="*", metavar="IP[:PORT] [CODE]",
                        default=None,
                        help="Connect by address without discovery/QR "
                             "(manual/remote path): guided when bare, "
                             "pairs only when needed — see examples below")
    parser.add_argument("--skip-pair", action="store_true", help="Skip pairing (already paired)")
    parser.add_argument("--manual", action="store_true", help="Force manual pairing mode")
    parser.add_argument("--wait", action="store_true",
                        help="Keep the single-device prompt (linger with "
                             "the QR to pair another phone) instead of "
                             "connecting immediately")
    parser.add_argument("--all", action="store_true", help="Connect to all discovered devices")
    parser.add_argument("--list", action="store_true", help="List discovered devices and exit")
    parser.add_argument("--debug", action="store_true",
                        help="Enable debug logging (adb commands, mDNS events)")
    parser.add_argument("--qr", choices=["auto", "sixel", "ascii"],
                        default="ascii",
                        help="QR rendering mode (default: ascii; auto probes "
                             "for Sixel but some terminals advertise Sixel in "
                             "DA1 without rendering it). The in-selector QR "
                             "is always ASCII — a Sixel image cannot be "
                             "redrawn inside curses")
    parser.add_argument("--on-connect", metavar="CMD", default=None,
                        help="Shell command to run after each successful connect "
                             "(device available as $AWC_TARGET)")
    parser.add_argument("--watch", action="store_true",
                        help="Watch mode: keep reconnecting until Ctrl+C "
                             "(pairs when needed, runs --on-connect every time)")
    parser.add_argument("--adb-path", default=None, metavar="PATH",
                        help="Path to the adb executable (default: adb on PATH)")
    args = parser.parse_args()

    if args.ip and args.direct is not None:
        parser.error("--direct and --ip are mutually exclusive")

    ckw = dict(skip_pair=args.skip_pair, manual=args.manual, qr_mode=args.qr,
               on_connect=args.on_connect)

    if args.debug:
        logging.basicConfig(level=logging.DEBUG,
                            format="%(levelname)s %(name)s: %(message)s")
        log.debug("args: %s", args)

    if args.adb_path:
        adb.ADB_PATH = args.adb_path
        log.debug("adb path: %s", adb.ADB_PATH)

    print("=== ADB WiFi Connection ===\n")

    # Manual/remote path: connect by address — no discovery, no QR
    if args.direct is not None:
        values = list(args.direct)
        if len(values) > 2:
            parser.error("--direct takes at most: IP[:PORT] [CODE]")
        target = values[0] if values else None
        code = values[1] if len(values) > 1 else None
        adb.warm_adb()
        result = _guarded(lambda: direct_connect(
            target=target, code=code, port=args.port,
            on_connect=args.on_connect))
        if not result:
            print("\nFailed.")
            sys.exit(1)
        if args.watch:
            ip = result.rsplit(":", 1)[0]
            return watch_loop([ip], **ckw)
        print(f"\nConnected: {result}")
        return result

    # Single-device mode: --ip specified
    if args.ip:
        if args.watch:
            return watch_loop([args.ip], **ckw)
        result = _guarded(lambda: connect(ip=args.ip, port=args.port, **ckw))

        print(f"\nConnected: {result}")
        return result

    # lyto-style: QR up front when pairing may be needed — pure local
    # render, nothing waited on. Discovery runs behind it.
    want_qr = (not (args.skip_pair or args.manual or args.list
                    or args.watch)
               and sys.stdin.isatty())
    session = start_pairing_session(
        args.qr, title="\n  QR CODE PAIRING") if want_qr else None
    adb.warm_adb()

    # Multi-device mode: discover all devices
    print("  Discovering devices...")
    devices = discover_all_phones(timeout=10)

    # Best-effort names for every consumer below (--list, selector,
    # headers): enrich right after discovery, before any routing
    if devices:
        devices = enrich_devices(devices)

    is_tty = sys.stdin.isatty()

    # Scan wins: whoever scanned is already the target — verified and
    # announced before any selection UI could appear for the others
    target = _early_scan_target(session, ckw["on_connect"])
    if target:
        return target

    if not devices:
        if session is None:
            # No QR on screen: nothing could arrive — fail fast (unchanged)
            print("  No devices found. Check WiFi and ensure Wireless debugging is enabled.")
            sys.exit(1)
        # A phone may be about to scan the QR already on screen: wait
        # for a VERIFIED scan (no countdown, no window — Ctrl+C quits).
        # A stray Enter is consumed and discarded: nothing to connect yet.
        print("  No devices found — scan the QR to pair a phone (Ctrl+C to quit)")
        stdin_live = True
        while True:
            target = _drain_one_scan(session, ckw["on_connect"])
            if target:
                return target
            if stdin_live:
                if poll_stdin(0.5):
                    # Enter consumed → keep polling a live stdin; EOF →
                    # stop polling (an always-ready EOF would busy-spin
                    # at 100% CPU) and pace the drains with sleep.
                    stdin_live = _discard_stdin_line()
            else:
                time.sleep(0.5)

    # --list flag: show devices and exit
    if args.list:
        _print_device_list(devices)
        return []

    # Single device found: one status block, then connect. A TTY gets
    # the Enter-to-connect prompt (draining scans while it waits); a
    # pipe has no Enter to give, so it connects straight away.
    if len(devices) == 1:
        rec = devices[0]
        ip = rec[0]
        print("  Found 1 device:")
        print(f"    {format_label(rec)}   {_status(rec)}")
        if args.watch:
            return watch_loop([ip], **ckw)
        if is_tty:
            qr_clause = (" · scan the QR to pair another phone"
                         if session is not None else "")
            if not args.wait and (rec[2] or _record_paired(rec)):
                # Eager: one ready phone — connect now, no prompt.
                result = _guarded(lambda: connect_single_device(
                    ip, session=session, paired=_record_paired(rec),
                    short_name=rec[1], connect_port=_record_port(rec),
                    **ckw))
                if result:
                    print(f"\nConnected: {result}")
                    return result
                print(f"  Could not connect to {ip} — press Enter to "
                      f"retry{qr_clause}")
            print()
            print(f"  Press Enter to connect{qr_clause} · Ctrl+C quit")
            while True:
                target = _drain_one_scan(session, ckw["on_connect"])
                if target:
                    return target
                if poll_stdin(0.5):
                    _discard_stdin_line()
                    break
        result = _guarded(lambda: connect_single_device(
            ip, session=session, paired=_record_paired(rec),
            short_name=rec[1], connect_port=_record_port(rec), **ckw))

        if result:
            print(f"\nConnected: {result}")
            return result
        else:
            print(f"\nFailed to connect to {ip}")
            sys.exit(1)

    # Multiple devices: selection UI — the live session's QR payload
    # rides in (rendered above the list) and its events drive the scan
    # that acts as confirm inside the selector
    if args.all:
        selected = list(range(len(devices)))
    else:
        selected = _guarded(lambda: select_devices(
            devices, is_tty=is_tty,
            qr_text=(session.qr_data if session else None),
            session=session))

    # A scan that landed while the selector was open: its record joins
    # the run (force-checked) instead of vanishing behind the UI
    _absorb_scanned(session, devices, selected)

    if not selected:
        print("\n  No devices selected.")
        sys.exit(0)

    # Desired-state selection: discovered but unchecked devices that are
    # currently connected get disconnected (applies to --watch too) —
    # status lines only for serials adb really removed (truthful output)
    selected_ips = {devices[i][0] for i in selected}
    for rec in devices:
        ip, short, connected = rec[:3]
        if connected and ip not in selected_ips:
            removed = adb.run_adb_disconnect(ip, short)
            for serial in removed:
                print(f"  Disconnected: {serial}")

    # Watch mode: stay in the reconnect loop instead of one-shot connects
    if args.watch:
        targets = [devices[i][0] for i in selected]
        return watch_loop(targets, **ckw)

    # Connect to selected devices
    print(f"\n{'='*50}")
    print(f"CONNECTING TO {len(selected)} DEVICE(S)")
    print(f"{'='*50}\n")

    results = []
    for idx in selected:
        # A scan that lands BETWEEN connects joins this run too: drain
        # before each call — the appended record then short-circuits as
        # already-connected inside its own iteration (one status line)
        _absorb_scanned(session, devices, selected)
        rec = devices[idx]
        ip, name, connected = rec[:3]
        print(f"\n--- {name} ({ip}) ---")
        result = _guarded(lambda: connect_single_device(
            ip, session=session, paired=_record_paired(rec),
            short_name=rec[1], connect_port=_record_port(rec), **ckw))

        if result:
            results.append(result)
        else:
            print(f"  Failed: {ip}")

    # Summary
    print(f"\n{'='*50}")
    print(f"SUMMARY: Connected {len(results)}/{len(selected)} devices")
    print(f"{'='*50}")
    for r in results:
        print(f"  {r}")

    if not results:
        sys.exit(1)

    return results


def run():
    """Console-script entry point.

    main() returns values for tests and the Python API, but a console
    wrapper must not hand them to sys.exit() — that would dump the repr
    and exit non-zero on success. Failures inside main() still exit
    non-zero via their own sys.exit() calls. Ctrl+C that escapes
    main() (e.g. during discovery, enrichment, or the 0-device wait —
    the selector itself sits behind main()'s _guarded phase guard)
    exits 1 with "Cancelled." instead of a raw traceback; SystemExit
    from _guarded passes through.
    """
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n  Cancelled.")
        sys.exit(1)
