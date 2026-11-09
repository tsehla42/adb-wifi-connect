# Deferred annotations: keeps the `str | None` / `list[str]` signature
# import-safe on the declared >=3.7 floor.
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time

log = logging.getLogger(__name__)

ADB_PATH = "adb"


def is_device_connected(ip):
    """Check if device is already connected via ADB and actually responds.
    Returns the port number if connected, None otherwise."""
    try:
        result = subprocess.run(
            [ADB_PATH, "devices"], capture_output=True, text=True, timeout=5
        )
        if f"{ip}:" not in result.stdout or "device" not in result.stdout:
            return None
        # Extract port and verify device actually responds
        for line in result.stdout.splitlines():
            if f"{ip}:" in line and "device" in line:
                serial = line.split()[0]
                port_str = serial.split(":")[1]
                # Disconnect stale entry first, then reconnect
                subprocess.run([ADB_PATH, "disconnect", serial],
                               capture_output=True, timeout=5)
                subprocess.run([ADB_PATH, "connect", serial],
                               capture_output=True, timeout=5)
                # Actually verify the device responds
                check = subprocess.run(
                    [ADB_PATH, "-s", serial, "shell", "echo", "test"],
                    capture_output=True, text=True, timeout=5
                )
                if check.returncode == 0:
                    return int(port_str)
        return None
    except Exception:
        return None


def connected_ips():
    """Set of IPs currently listed as connected by `adb devices`.
    Exceptions are swallowed (missing/unreachable adb -> empty set)."""
    # Get all connected devices once, then check each discovered IP
    connected_ips = set()
    try:
        proc = subprocess.run(
            [ADB_PATH, "devices"], capture_output=True, text=True, timeout=5
        )
        for line in proc.stdout.splitlines():
            if "\tdevice" in line:
                serial = line.split()[0]
                parts = serial.split(":")
                if len(parts) == 2:
                    connected_ips.add(parts[0])
    except Exception:
        pass
    return connected_ips


def connected_serials():
    """Set of serials currently in state `device` per one `adb devices` run.
    Covers both `ip:port` serials and the mDNS-form serials that TLS
    connections show up as. Exceptions are swallowed (-> empty set)."""
    serials = set()
    try:
        proc = subprocess.run(
            [ADB_PATH, "devices"], capture_output=True, text=True, timeout=5
        )
        for line in proc.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1].strip() == "device":
                serials.add(parts[0].strip())
    except Exception:
        pass
    return serials


def read_identity(serial):
    """Read a device's human-readable identity over adb in ONE quiet call.
    Returns {"marketname", "brand", "model", "device_name"} with "" for
    blank lines, or None on any exception/nonzero exit. Never raises."""
    argv = [ADB_PATH, "-s", serial, "shell",
            "getprop ro.product.marketname; getprop ro.product.brand; "
            "getprop ro.product.model; settings get global device_name"]
    log.debug("Running: %s", " ".join(argv))
    try:
        result = subprocess.run(argv, capture_output=True, text=True,
                                timeout=3)
        log.debug("adb identity rc=%s stdout=%r",
                  result.returncode, result.stdout)
        if result.returncode != 0:
            return None
        lines = (result.stdout or "").splitlines()
        lines += [""] * (4 - len(lines))  # missing tail lines -> ""
        marketname, brand, model, device_name = (
            line.strip() for line in lines[:4])
        if device_name == "null":
            # `settings get global device_name` prints the literal
            # sentinel `null` when the key is unset — an Android
            # placeholder, not a name (raw, format_label would render
            # `Samsung null (SM-G9960)`). Normalize ONLY the settings
            # line; getprop lines already use "" for empty.
            device_name = ""
        return {"marketname": marketname, "brand": brand, "model": model,
                "device_name": device_name}
    except Exception:
        return None


def _adb(argv, timeout, echo=True):
    log.debug("Running: %s", " ".join(str(c) for c in argv))
    if echo:
        print(f"  -> {' '.join(str(c) for c in argv)}")
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    log.debug("adb %s rc=%s stdout=%r", argv[1], result.returncode, result.stdout)
    if echo:
        print(f"  <- {result.stdout.strip()}")
    return result.stdout


def run_adb_pair(ip, port, code):
    """Run adb pair command. Returns True on success."""
    stdout = _adb([ADB_PATH, "pair", f"{ip}:{port}", code], 15)
    return "Successfully paired" in stdout


def run_adb_connect(ip, port, timeout=10, echo=True):
    """Run adb connect command. Returns True on success."""
    stdout = _adb([ADB_PATH, "connect", f"{ip}:{port}"], timeout, echo=echo)
    return "connected to" in stdout


def run_adb_disconnect(ip: str, short_name: str | None = None) -> list[str]:
    """Disconnect every live serial belonging to ip (desired-state selection).

    Candidates come from ONE `adb devices` snapshot (`connected_serials`):
    every serial starting with `ip:` plus the exact mDNS-form serial
    `adb-{short_name}._adb-tls-connect._tcp` when it is live. Each
    candidate is disconnected by its EXACT serial — `adb disconnect <bare
    ip>` makes adb misparse `ip:5555` and silently no-op. Returns the
    serials adb confirmed with its `disconnected` success token; zero
    candidates -> [] and nothing is run.
    """
    live = connected_serials()
    candidates = sorted(s for s in live if s.startswith(f"{ip}:"))
    if short_name:
        mdns_serial = f"adb-{short_name}._adb-tls-connect._tcp"
        if mdns_serial in live:
            candidates.append(mdns_serial)
    removed = []
    for serial in candidates:
        try:
            stdout = _adb([ADB_PATH, "disconnect", serial], 5)
        except Exception:
            log.debug("adb disconnect %s failed", serial, exc_info=True)
            continue
        if (stdout or "").startswith("disconnected"):
            removed.append(serial)
    return removed


def warm_adb():
    """Start the adb server in the background so the first probe after
    startup is warm (adb's cold start can cost 1-2s). Never raises."""

    def _run():
        try:
            subprocess.run([ADB_PATH, "start-server"],
                           capture_output=True, timeout=10)
        except Exception:
            pass

    threading.Thread(target=_run, daemon=True).start()


def run_on_connect(cmd, target):
    """Run the --on-connect hook after a successful connection.

    The connected device is passed in the AWC_TARGET environment
    variable. Hook failures are reported but never fatal.
    """
    if not cmd:
        return
    print(f"  Running --on-connect: {cmd}")
    try:
        subprocess.run(cmd, shell=True, timeout=60,
                       env={**os.environ, "AWC_TARGET": target})
    except Exception as exc:
        print(f"  [--on-connect failed: {exc}]")
        log.debug("on-connect hook failed", exc_info=True)


def connect_with_retries(ip, port, retries=3, delay=1.0):
    """Try connecting to the same port multiple times.
    Returns True on success."""
    for attempt in range(retries):
        if attempt > 0:
            print(f"  Retry {attempt}/{retries-1}...")
            time.sleep(delay)
        if run_adb_connect(ip, port):
            return True
    return False


def scan_for_adb_port(ip, timeout=15):
    """Scan likely port ranges for ADB connection.
    Uses parallel threads to try multiple ports quickly.
    Returns port number on success, None on failure."""
    from concurrent.futures import ThreadPoolExecutor

    # Ports to try: common defaults + likely range
    common_ports = [5555, 5556]
    range_ports = list(range(30000, 48001, 100))  # Sample every 100th port first
    all_ports = common_ports + range_ports

    found_port = [None]
    lock = threading.Lock()
    stop_event = threading.Event()

    def _try_port(port, gate, slot):
        if gate.is_set():
            return
        try:
            if run_adb_connect(ip, port, timeout=3, echo=False):
                with lock:
                    if slot[0] is None:
                        slot[0] = port
                        gate.set()
        except Exception:
            pass

    print(f"  Scanning {len(all_ports)} ports (timeout {timeout}s)...")
    with ThreadPoolExecutor(max_workers=20) as pool:
        futures = [pool.submit(_try_port, p, stop_event, found_port)
                   for p in all_ports]
        deadline = time.time() + timeout
        while time.time() < deadline and found_port[0] is None:
            time.sleep(0.5)
        stop_event.set()

    coarse_result = found_port[0]

    if not coarse_result:
        return None

    print(f"  Found ADB on port {coarse_result}")
    if coarse_result in common_ports:
        return coarse_result

    # Do a finer scan around the found port
    fine_start = max(30000, coarse_result - 50)
    fine_end = min(48000, coarse_result + 50)
    # Refine the scan with a fresh event to avoid races with lingering coarse threads
    fine_ports = list(range(fine_start, fine_end + 1))
    fine_event = threading.Event()
    fine_found = [None]

    with ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_try_port, p, fine_event, fine_found)
                   for p in fine_ports]
        deadline = time.time() + 5
        while time.time() < deadline and fine_found[0] is None:
            time.sleep(0.2)
        fine_event.set()

    if fine_found[0]:
        return fine_found[0]
    # Fine scan failed — fall back to coarse result
    return coarse_result
