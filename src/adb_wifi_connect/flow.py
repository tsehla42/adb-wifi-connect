import logging
import queue
import subprocess
import sys
import threading
import os
import time
from concurrent.futures import ThreadPoolExecutor

from . import adb
from .qr import generate_code, generate_device_name, format_qr_data, display_qr
from .adb import (is_device_connected, run_adb_pair, run_adb_connect,
                  connect_with_retries, scan_for_adb_port, run_on_connect)
from .mdns import (discover_connection_port, discover_all_phones,
                   _resolve_host, _browse, _short_service_name,
                   MDNS_TYPE, MDNS_CONNECT_TYPE, Zeroconf)
from .ui import (ask_pairing_code, ask_pairing_port, ask_connection_port,
                 ask_ip, poll_stdin, select_devices)

log = logging.getLogger(__name__)

# Session-long silent listener: one pairing browse tick, and the cap for
# resolving a freshly paired phone's own connection port (seconds).
SCAN_TICK = 2
CONNECT_CAP = 10


class PairingSession:
    """Credentials + session-long silent scan listener shared across a run.

    start() renders the QR immediately (lyto-style — nothing is waited
    for first); a background listener browses pairing services for as
    long as the session lives and turns EVERY scan into a queue event
    for the main thread to route:
      {"kind": "scanned", "ip", "short", "port", "target", "identity"}
        once pair + connect + identity read are done, or
      {"kind": "scan-failed", "ip"}
        when pairing or port resolution fails — the listener keeps
        running, so the next scan is still served (retry-capable).
    `done`/`result` record the FIRST scan only (result set just before
    done — first success wins, later scans never overwrite them) as
    legacy state; every live wait site consumes `events` instead: each
    queued scan serves exactly ONE wait (`wait_for_scanned_event`), and
    a wait with an empty queue blocks for a NEW event — `done` never
    satisfies it.
    The listener never prints: log.debug only — callers own all
    user-facing output.
    """

    def __init__(self, qr_mode="auto"):
        self.pair_code = generate_code(6)
        self.pair_name = generate_device_name()
        self.qr_data = format_qr_data(self.pair_name, self.pair_code)
        self.qr_mode = qr_mode
        self.done = threading.Event()
        self.result = None
        self.events = queue.Queue()
        self._stop = threading.Event()
        self._thread = None

    def start(self, title=None):
        """Render the QR now and begin listening. Returns self."""
        self.started_at = time.time()
        if title:
            print(title)
        print("  On your phone: Settings -> Developer options -> Wireless debugging")
        print("  Tap 'Pair device with QR code'\n")
        if Zeroconf is None:
            # Main-thread output (the listener never prints): without
            # zeroconf no scan can ever be heard — say so, then still
            # render the QR (a pure local operation).
            print("[zeroconf not installed — QR mode unavailable]")
            print("[Install: pip install zeroconf]")
        display_qr(self.qr_data, mode=self.qr_mode)
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()
        return self

    def stop(self):
        """End the listener: it exits within the current browse tick
        (≈ SCAN_TICK). Idempotent."""
        self._stop.set()

    # --- listener (background thread): side effects only, zero prints ---

    def _listen(self):
        """Session-long loop: one silent pairing tick at a time; every
        scan is served as an event. Never dies on a bad iteration —
        failures are logged (debug) and the loop keeps its cadence."""
        if Zeroconf is None:
            # mdns._browse's caller contract: callers check themselves.
            # No browsing, no retry loop, no print — start() already
            # showed the user-facing hint on the main thread.
            log.debug("zeroconf missing — scan listener not started")
            return
        while not self._stop.is_set():
            try:
                info = self._next_pairing_service()
                if info is not None:
                    self._serve_scan(info)
            except Exception:
                log.debug("scan listener iteration failed", exc_info=True)
                time.sleep(SCAN_TICK)  # keep tick cadence, never hot-loop

    def _next_pairing_service(self):
        """One silent pairing browse tick: the first pairing record with
        a server, or None when nothing was found this tick."""
        found = [None]

        def on_info(service_type, info):
            if getattr(info, "server", None):
                found[0] = info
                return True
            return False

        _browse([MDNS_TYPE], SCAN_TICK, on_info)
        return found[0]

    def _serve_scan(self, info):
        """Pair the phone that scanned, resolve ITS connection port, then
        queue the outcome and update result/done (result: first success
        wins). Either outcome continues the outer loop — failures queue
        'scan-failed' and stay retryable."""
        ip = _resolve_host(info.server)
        pairing_port = info.port
        short = _short_service_name(info.name)
        log.debug("scan from %s (%s), pairing port %s", ip, short,
                  pairing_port)

        if not run_adb_pair(ip, pairing_port, self.pair_code):
            log.debug("pair failed for %s — queueing scan-failed", ip)
            self.events.put({"kind": "scan-failed", "ip": ip})
            return

        port = self._connect_port_for(ip)
        if port is None:
            log.debug("no connect port for %s — queueing scan-failed", ip)
            self.events.put({"kind": "scan-failed", "ip": ip})
            return

        if not run_adb_connect(ip, port, echo=False):
            log.debug("adb connect failed for %s:%s", ip, port)
        identity = adb.read_identity(f"{ip}:{port}")
        self.events.put({"kind": "scanned", "ip": ip, "short": short,
                         "port": port, "target": f"{ip}:{port}",
                         "identity": identity})
        if self.result is None:
            # Contract: result is set on FIRST success — later scans
            # still queue their own events but never overwrite it.
            self.result = (ip, pairing_port)
        self.done.set()

    def _connect_port_for(self, ip):
        """The connection port of THIS phone only, within CONNECT_CAP.
        Multi-device safety: infos resolving to any other IP are ignored
        (an unfiltered browse could pair phone A then connect phone B).
        None when no matching record appears in time."""
        found = [None]

        def on_info(service_type, info):
            server = getattr(info, "server", None)
            if server and _resolve_host(server) == ip:
                found[0] = info
                return True
            return False

        _browse([MDNS_CONNECT_TYPE], CONNECT_CAP, on_info)
        return None if found[0] is None else found[0].port


def start_pairing_session(qr_mode="auto", title=None):
    """Create, render, and start a PairingSession."""
    return PairingSession(qr_mode).start(title=title)


def _candidate(rec):
    """A record enrichment may act on: paired per the discovery snapshot.
    Legacy 3-tuples carry no paired slot -> never candidates (identity
    stays None, no adb traffic)."""
    try:
        return len(rec) > 3 and bool(rec[3])
    except Exception:
        return False


def _live_serial(ip, short_name, connect_port, live):
    """An already-live serial for this phone, if any: prefer ip:port,
    then any other ip:port form for the same IP, then the mDNS-form
    serial built from short_name. None -> nothing is live, connecting
    would be needed."""
    if connect_port:
        exact = f"{ip}:{connect_port}"
        if exact in live:
            return exact
    for serial in live:
        if serial.startswith(f"{ip}:"):
            return serial
    if short_name:
        mdns = f"adb-{short_name}._adb-tls-connect._tcp"
        if mdns in live:
            return mdns
    return None


def _enrich_one(rec, live):
    """Best-effort identity for ONE paired record. Never raises, never
    disconnects: any transport it opens is left connected (the next run
    starts warmer). is_connected/is_paired are discovery snapshots and
    are never touched."""
    try:
        ip = rec[0]
        short_name = rec[1]
        connect_port = rec[4] if len(rec) > 4 else None
        serial = _live_serial(ip, short_name, connect_port, live)
        if serial is None:
            if not connect_port:
                return None  # paired snapshot without a port: nothing to dial
            if not run_adb_connect(ip, connect_port, timeout=3, echo=False):
                return None
            serial = f"{ip}:{connect_port}"
        return adb.read_identity(serial)
    except Exception:
        return None


def enrich_devices(devices):
    """Fill identity (index 5) of discovered devices, best effort.

    Paired records are read over an already-live serial when one exists
    (ip:port preferred, else the mDNS-form serial) and connected first
    otherwise ({ip}:{connect_port}, 3s cap, quiet); unpaired records are
    never connected and keep identity None. Candidates run in parallel
    (ThreadPoolExecutor) behind a single `adb devices` snapshot, bounded
    by the per-device caps; every per-device failure/timeout degrades to
    identity None. Never raises, never disconnects anything, never
    mutates the is_connected/is_paired snapshots, and prints the banner
    only when at least one candidate exists.
    """
    try:
        records = list(devices) if devices else []
    except Exception:
        return []  # never raise: nothing sensible to enrich
    candidates = [i for i, rec in enumerate(records) if _candidate(rec)]

    if not candidates:
        return [tuple(rec) if isinstance(rec, (list, tuple)) else rec
                for rec in records]

    print("  Reading device names…")
    try:
        live = adb.connected_serials()
    except Exception:
        live = set()

    identities = {}
    try:
        with ThreadPoolExecutor(max_workers=len(candidates)) as pool:
            futures = {i: pool.submit(_enrich_one, records[i], live)
                       for i in candidates}
            for i, future in futures.items():
                try:
                    identities[i] = future.result()
                except Exception:
                    identities[i] = None
    except Exception:
        identities = {}

    enriched = []
    for i, rec in enumerate(records):
        try:
            if i in identities:
                padded = list(rec) + [None] * (6 - len(rec))
                padded[5] = identities[i]
                enriched.append(tuple(padded))
            else:
                enriched.append(tuple(rec) if isinstance(rec, (list, tuple))
                                else rec)
        except Exception:
            enriched.append(rec)
    return enriched


def parse_direct_target(text):
    """Parse 'ip' or 'ip:port' for --direct.
    Returns (host, port|None), or (None, None) when invalid."""
    host, sep, port_str = text.partition(":")
    if not host:
        return None, None
    if not sep:
        return host, None
    try:
        port = int(port_str)
    except ValueError:
        return None, None
    if not 1 <= port <= 65535:
        return None, None
    return host, port


def direct_connect(target=None, code=None, port=None, on_connect=None):
    """Manual/secondary path: connect by address, pairing only if needed.

    target: 'ip' or 'ip:port' (prompted when None). With `code`, the
    target port is the PAIRING port and pairing happens first; without
    it, connect is attempted first (may already be paired) and pairing
    is only prompted when the device is unreachable. `port` (--port) is
    always the connection port. Returns "ip:port" or None.
    """
    if target is None:
        ip = ask_ip()
        tport = None
    else:
        ip, tport = parse_direct_target(target)
        if not ip:
            print(f"  Invalid target: {target} (expected ip or ip:port)")
            return None

    def success(t):
        print(f"  Connected: {t}")
        run_on_connect(on_connect, t)
        return t

    already = is_device_connected(ip)
    if already:
        print(f"  Already connected: {ip}:{already}")
        return success(f"{ip}:{already}")

    paired = False
    if code is not None:
        pair_port = tport if tport is not None else ask_pairing_port()
        print(f"\n  Pairing with {ip}:{pair_port}...")
        if not run_adb_pair(ip, pair_port, code):
            print("  Pairing failed.")
            return None
        print("  Paired successfully!")
        paired = True

    if not paired:
        # Connect first: explicit port -> mDNS -> scan (already-paired
        # devices never see a prompt)
        connect_port = port if port is not None else tport
        if connect_port is not None:
            if connect_with_retries(ip, connect_port, retries=3, delay=1.0):
                return success(f"{ip}:{connect_port}")
        else:
            cip, cport = discover_connection_port(timeout=8)
            if cport:
                target_ip = _resolve_host(cip) if cip else ip
                if connect_with_retries(target_ip, cport,
                                        retries=3, delay=1.0):
                    return success(f"{target_ip}:{cport}")
            scan_port = scan_for_adb_port(ip, timeout=10)
            if scan_port:
                return success(f"{ip}:{scan_port}")
        # Unreachable -> guide pairing (the original manual way)
        print(f"\n  Could not reach {ip} — pair it first")
        print("  On your phone: Settings -> Developer options "
              "-> Wireless debugging")
        print("  Tap 'Pair device with pairing code'")
        entered = ask_pairing_code(allow_empty=True)
        if not entered:
            print("  No pairing code given.")
            return None
        pport = ask_pairing_port()
        if not run_adb_pair(ip, pport, entered):
            print("  Pairing failed.")
            return None
        print("  Paired successfully!")
        paired = True

    # After pairing: resolve the connection port (it differs from the
    # pairing port; mDNS can't help across the internet)
    if port is not None:
        connect_port = port
    else:
        print("  Note: the connection port differs from the pairing port.")
        connect_port = ask_connection_port(optional=True)
    if connect_port is not None:
        if connect_with_retries(ip, connect_port, retries=3, delay=1.0):
            return success(f"{ip}:{connect_port}")
        print(f"  Failed to connect to {ip}:{connect_port}")
        return None
    cip, cport = discover_connection_port(timeout=8)
    if cport:
        target_ip = _resolve_host(cip) if cip else ip
        if connect_with_retries(target_ip, cport, retries=3, delay=1.0):
            return success(f"{target_ip}:{cport}")
    scan_port = scan_for_adb_port(ip, timeout=10)
    if scan_port:
        return success(f"{ip}:{scan_port}")
    print("  Auto-discovery failed — enter the connection port.")
    final_port = ask_connection_port()
    if connect_with_retries(ip, final_port, retries=3, delay=1.0):
        return success(f"{ip}:{final_port}")

    print(f"  Failed to connect to {ip}")
    return None


def _serial_alive(serial):
    """True when an already-live adb transport answers `shell echo test`
    (the is_device_connected verification pattern). Never raises."""
    try:
        check = subprocess.run(
            [adb.ADB_PATH, "-s", serial, "shell", "echo", "test"],
            capture_output=True, text=True, timeout=5)
        return check.returncode == 0
    except Exception:
        return False


def _verify_scanned_event(event):
    """Confirm a queued scan has a LIVE transport before flow commits to
    it — the listener's connect is best-effort. Echo-test
    (is_device_connected also reconnects stale entries); if it does not
    confirm, retry the connect once (quiet) and re-verify. Mirrors
    `cli._verify_scanned` semantically — flow never imports cli
    helpers (layering)."""
    if is_device_connected(event.get("ip")):
        return True
    port = event.get("port")
    if port is None:
        return False
    run_adb_connect(event.get("ip"), port, echo=False)
    return bool(is_device_connected(event.get("ip")))


def wait_for_scanned_event(session, tick=0.5):
    """Wait for ONE fresh, transport-verified 'scanned' event — or
    Enter → "manual".

    Fresh BY CONSTRUCTION: consumes `session.events` (the queue), never
    `session.done`/`session.result` — a scan an earlier wait already
    took cannot satisfy this one, and the returned payload carries its
    OWN ip/short/port/target/identity. The session's LISTENER has
    already paired + connected this scan (pairing owns exactly one
    place — a successful pair deregisters the pairing service, so any
    re-pair here would fail against a closed port): this wait never
    calls run_adb_pair.

    Semantics mirror the cli wait sites (drain → verify → keep
    waiting), implemented here so flow never imports cli helpers:
    - 'scan-failed' → prints its retry line, wait continues
    - 'scanned' with a dead transport → prints the
      `Scan paired … connect failed` retry line, wait continues (the
      still-running listener serves the rescan)
    - Enter → "manual" (EOF stdin is noticed once and paced with
      sleep — never busy-spun)
    No countdown, no window, nothing timed is printed. Ctrl+C is NOT
    caught here — the run() net owns it."""
    stdin_live = True
    while True:
        # One non-blocking drain pass: every queued event is consumed
        # exactly once, in order.
        while True:
            try:
                event = session.events.get_nowait()
            except queue.Empty:
                break
            kind = event.get("kind")
            if kind == "scan-failed":
                print(f"  Pairing failed for {event.get('ip')} — scan again")
            elif kind == "scanned":
                if _verify_scanned_event(event):
                    return event
                print(f"  Scan paired {event.get('ip')} but connect failed — "
                      f"scan again or check Wireless debugging")
        # Nothing usable queued: interleave Enter detection with a
        # bounded wait for a NEW event (poll blocks up to `tick`, then
        # the next loop's drain pass reads whatever landed meanwhile).
        if stdin_live and poll_stdin(tick):
            line = sys.stdin.readline()
            if line == "":
                stdin_live = False  # EOF: stop polling a forever-ready stdin
            else:
                return "manual"
        elif not stdin_live:
            time.sleep(tick)  # paced drains instead of re-polling EOF stdin


def connect_single_device(ip, port=None, skip_pair=False, manual=False,
                          qr_mode="auto", on_connect=None, session=None,
                          paired=False, short_name=None,
                          connect_port=None):
    """
    Connect to a single device. Returns "ip:port" on success, None on failure.
    When port is provided, skips mDNS discovery and port scanning.
    An optional live PairingSession provides the (already rendered) QR
    credentials instead of generating a new QR here; its pairing wait
    consumes `session.events` (fresh events only — done/result are never
    keyed on), and the LISTENER owns pairing: a scanned event is
    verified and completed here, never re-paired.
    paired=True skips the pairing branch entirely (no session, no QR wait,
    no manual prompts); short_name + connect_port enable reusing an already
    live mDNS-form transport instead of opening a duplicate one (spec §5).
    """
    def success(target, announce=True):
        # Announce fresh connects only — already-announced states must
        # not produce a second "Connected:" line.
        if announce:
            print(f"  Connected: {target}")
        run_on_connect(on_connect, target)
        return target

    # Already connected?
    connected_port = is_device_connected(ip)
    if connected_port:
        print(f"  Already connected: {ip}:{connected_port}")
        return success(f"{ip}:{connected_port}", announce=False)

    # Auto-detect non-interactive mode
    if not sys.stdin.isatty():
        manual = True

    # Pairing (if needed) — already-paired devices never see a QR wait
    if not skip_pair and not paired:
        if not manual:
            # Reuse the live session (QR already on screen); its listener
            # pairs every scan itself and queues the outcome for THIS
            # wait to consume.
            if session is None:
                session = start_pairing_session(
                    qr_mode, title=f"\n  QR CODE PAIRING for {ip}")

            print("\n  Waiting for the QR scan — press Enter for manual mode, "
                  "Ctrl+C to quit\n")

            outcome = wait_for_scanned_event(session)

            if outcome == "manual":
                print("  Switching to manual mode...")
                manual = True
            else:
                event = outcome
                scanned_ip = event.get("ip")
                if scanned_ip != ip:
                    print(f"  Scanned phone {scanned_ip} selected instead of "
                          f"{ip}")
                    ip = scanned_ip
                # The listener ALREADY paired + connected this scan —
                # never re-pair (the pairing service deregisters on a
                # successful pair). Target/transport come from the
                # event payload: straight to the connection phase.
                port = event.get("port")
                short_name = event.get("short")
                connect_port = event.get("port")

        if manual:
            print(f"\n  MANUAL PAIRING for {ip}")
            print("  On your phone: Settings -> Developer options -> Wireless debugging")
            print("  Tap 'Pair device with pairing code'\n")

            pair_code = ask_pairing_code()
            pair_port = ask_pairing_port()

            print(f"\n  Pairing...")
            if not run_adb_pair(ip, pair_port, pair_code):
                print("  Pairing failed.")
                return None
            print("  Paired successfully!")

    # Connection
    print(f"\n  Connecting to {ip}...")

    # Transport reuse (spec §5) — FIRST check: a phone already live under
    # its mDNS-form serial needs no second adb connect and no port scan.
    # Verify it answers, then return through the normal success path.
    if short_name and connect_port is not None:
        live_serial = f"adb-{short_name}._adb-tls-connect._tcp"
        if live_serial in adb.connected_serials() and _serial_alive(live_serial):
            return success(f"{ip}:{connect_port}")

    # If port specified, skip discovery and connect directly
    if port is not None:
        if connect_with_retries(ip, port, retries=3, delay=1.0):
            return success(f"{ip}:{port}")
        print(f"  Failed to connect to {ip}:{port}")
        return None

    # Try mDNS port discovery
    connect_ip, connect_port = discover_connection_port(timeout=10)
    if connect_port:
        if connect_ip:
            connect_ip = _resolve_host(connect_ip)
            ip = connect_ip
        if connect_with_retries(ip, connect_port, retries=3, delay=1.0):
            return success(f"{ip}:{connect_port}")

    # Fall back to port scanning
    scan_port = scan_for_adb_port(ip, timeout=15)
    if scan_port:
        return success(f"{ip}:{scan_port}")

    # Last resort: common ports
    for try_port in [5555, 5556]:
        if run_adb_connect(ip, try_port):
            return success(f"{ip}:{try_port}")

    # Final fallback: ask user for connection port
    if sys.stdin.isatty():
        print(f"\n  Auto-discovery failed for {ip}")
        manual_port = ask_connection_port()
        if connect_with_retries(ip, manual_port, retries=3, delay=1.0):
            return success(f"{ip}:{manual_port}")

    print(f"  Failed to connect to {ip}")
    return None


def connect(ip=None, port=None, skip_pair=False, manual=False, qr_mode="auto",
            on_connect=None):
    """
    Main connection flow.
    Returns connected "ip:port" string on success.
    """
    try:
        return _connect_inner(ip, port, skip_pair, manual, qr_mode, on_connect)
    except KeyboardInterrupt:
        print("\n\n  Cancelled.")
        sys.exit(1)


def _connect_inner(ip, port, skip_pair, manual, qr_mode="auto",
                   on_connect=None):
    # If IP specified, use single-device flow
    if ip is not None:
        return connect_single_device(ip, port=port, skip_pair=skip_pair,
                                     manual=manual, qr_mode=qr_mode,
                                     on_connect=on_connect)

    # Auto-discover all devices
    print("  Discovering phone IP via mDNS...")
    devices = discover_all_phones(timeout=8)

    if not devices:
        default_ip = os.environ.get("AWC_DEFAULT_IP")
        if default_ip:
            print(f"  mDNS lookup failed, using AWC_DEFAULT_IP ({default_ip})")
            return connect_single_device(default_ip, skip_pair=skip_pair,
                                         manual=manual, qr_mode=qr_mode,
                                         on_connect=on_connect)
        print("  No devices found via mDNS and AWC_DEFAULT_IP is not set.")
        print("  Hint: export AWC_DEFAULT_IP=<phone-ip>, put it in a .env "
              "file, or pass --ip.")
        return None

    # Single device: connect directly
    if len(devices) == 1:
        ip = devices[0][0]
        print(f"  Phone found at {ip}")
        return connect_single_device(ip, skip_pair=skip_pair, manual=manual,
                                     qr_mode=qr_mode, on_connect=on_connect)

    # Multiple devices: selection UI
    is_tty = sys.stdin.isatty()
    selected = select_devices(devices, is_tty=is_tty)

    if not selected:
        print("\n  No devices selected.")
        sys.exit(0)

    # Connect to first selected device (backward compat for connect() API)
    idx = selected[0]
    ip = devices[idx][0]
    print(f"\n  Connecting to {ip}...")
    return connect_single_device(ip, skip_pair=skip_pair, manual=manual,
                                 qr_mode=qr_mode, on_connect=on_connect)
