import logging
import threading
import time

from .adb import connected_serials

log = logging.getLogger(__name__)

try:
    from zeroconf import Zeroconf, ServiceBrowser
except ImportError:
    Zeroconf = None
    ServiceBrowser = None

MDNS_TYPE = "_adb-tls-pairing._tcp.local."
MDNS_CONNECT_TYPE = "_adb-tls-connect._tcp.local."


def _resolve_host(server):
    """Resolve mDNS hostname to IP. Returns IP string, or original server if already an IP."""
    import socket
    # If it's already an IP address, return as-is
    try:
        socket.inet_aton(server)
        return server
    except OSError:
        pass
    # Resolve hostname
    try:
        return socket.gethostbyname(server)
    except socket.gaierror:
        return server  # Return hostname as-is, ADB can handle it


def _short_service_name(name):
    """Strip the mDNS service decoration from a service instance name:
    'adb-A1B2C3D4E5F6-xyz123._adb-tls-connect._tcp.local.' -> 'A1B2C3D4E5F6-xyz123'.
    Names without the decoration come back unchanged."""
    base = name.split("._adb-tls", 1)[0]
    if base != name and base.startswith("adb-"):
        return base[len("adb-"):]
    return base


def _browse(types, timeout, on_info, grace=None):
    """Browse mDNS service types until timeout, stop-signal, or grace expiry.

    on_info(service_type, info) receives each discovered service; return
    True to stop browsing immediately. With grace, browsing also stops
    `grace` seconds after the first info with a truthy `.server` is seen
    (set-once grace window — mirrors the original found_devices guard).
    Always cancels every browser and closes the Zeroconf instance.
    Callers must check `Zeroconf is None` themselves.
    """
    lock = threading.Lock()
    state = {"stopped": False, "seen": False}

    class Listener:
        def add_service(self, zc, type, name):
            info = zc.get_service_info(type, name)
            if not info:
                return
            stop = on_info(type, info)
            with lock:
                if stop:
                    state["stopped"] = True
                if getattr(info, "server", None):
                    state["seen"] = True

        def remove_service(self, zc, type, name):
            pass

        def update_service(self, zc, type, name):
            pass

    zc = Zeroconf()
    listener = Listener()
    browsers = [ServiceBrowser(zc, svc_type, listener) for svc_type in types]

    deadline = time.time() + timeout
    grace_deadline = None
    while time.time() < deadline:
        with lock:
            stopped = state["stopped"]
            seen = state["seen"]
        if stopped:
            break
        if grace is not None and seen:
            if grace_deadline is None:
                grace_deadline = time.time() + grace
            elif time.time() >= grace_deadline:
                break
        time.sleep(0.5)

    for browser in browsers:
        browser.cancel()
    zc.close()


def discover_pairing_service(timeout=30, announce=None):
    """
    Listen for phone's mDNS pairing service.
    Returns (ip, port) if found, None on timeout.
    announce: the user-facing window to print (the browse itself may run
    slightly longer; defaults to timeout).
    """
    if Zeroconf is None:
        print("[zeroconf not installed — QR mode unavailable]")
        print("[Install: pip install zeroconf]")
        return None

    shown = announce if announce is not None else timeout
    print(f"  Listening for pairing service (timeout {shown}s)...")

    result = [None]

    def on_info(service_type, info):
        result[0] = (info.server, info.port)
        return True

    _browse([MDNS_TYPE], timeout, on_info)

    return result[0]


def discover_connection_port(timeout=10):
    """
    Discover the ADB connection port via mDNS after pairing.
    Uses zeroconf to find the _adb-tls-connect service.
    Returns (server, port) tuple or (None, None) if not found.
    server may be an IP or hostname.
    """
    if Zeroconf is None:
        return None, None

    result = [None, None]

    def on_info(service_type, info):
        result[0] = info.server
        result[1] = info.port
        return True

    _browse([MDNS_CONNECT_TYPE], timeout, on_info)

    return result[0], result[1]


def discover_all_phones(timeout=15, grace=1.0):
    """
    Discover ALL Android devices via mDNS.
    Returns list of 6-tuples (ip, short_name, is_connected, is_paired,
    connect_port, identity): connect_port is the _adb-tls-connect record's
    port (None when only the pairing service was seen), identity is a
    reserved slot — always None at discovery.
    Returns early `grace` seconds after the first device is seen —
    devices still arriving inside that window are collected too.
    """
    if Zeroconf is None:
        return []

    lock = threading.Lock()
    found_devices = {}  # ip -> (name, is_paired, connect_port)

    def on_info(service_type, info):
        if info.server:
            ip = _resolve_host(info.server)
            if ip:
                log.debug("mDNS: %s %s -> %s:%s", service_type, info.name, ip, info.port)
                with lock:
                    is_connect = service_type == MDNS_CONNECT_TYPE
                    if ip not in found_devices:
                        found_devices[ip] = (info.name, is_connect,
                                             info.port if is_connect else None)
                    elif is_connect:
                        # connect record after a pairing record: paired
                        # now, and carries the connection port
                        name, _, _ = found_devices[ip]
                        found_devices[ip] = (name, True, info.port)
        return False

    _browse([MDNS_CONNECT_TYPE, MDNS_TYPE], timeout, on_info, grace=grace)

    serials = connected_serials()

    result = []
    for ip, (name, is_paired, connect_port) in found_devices.items():
        short_name = _short_service_name(name)
        mdns_serial = f"adb-{short_name}._adb-tls-connect._tcp"
        is_connected = mdns_serial in serials or any(
            s.startswith(f"{ip}:") for s in serials)
        result.append((ip, short_name, is_connected, is_paired,
                       connect_port, None))

    return result
