import logging
import threading

from .mdns import (Zeroconf, ServiceBrowser, MDNS_TYPE, MDNS_CONNECT_TYPE,
                   _resolve_host, discover_all_phones)
from .flow import connect_single_device
from .adb import is_device_connected

log = logging.getLogger(__name__)


class WatchListener:
    """zeroconf listener tracking advertised state for watched devices.

    state[ip] is True (connect service seen -> phone is paired),
    "pair" (only the pairing service is advertised), or False
    (services removed -> phone gone or wireless debugging off).

    When a wake event is provided it is set on every add — letting a
    watcher react immediately instead of waiting for its next tick.
    """

    def __init__(self, state, wake=None):
        self.state = state
        self.wake = wake
        self.lock = threading.Lock()
        self.names = {}  # service instance name -> ip, for removals

    def add_service(self, zc, type, name):
        try:
            info = zc.get_service_info(type, name)
            if not info or not info.server:
                return
            ip = _resolve_host(info.server)
            if not ip:
                return
            with self.lock:
                self.names[name] = ip
                if type == MDNS_CONNECT_TYPE:
                    self.state[ip] = True
                elif self.state.get(ip) is not True:
                    self.state[ip] = "pair"
            if self.wake is not None:
                self.wake.set()
        except Exception:
            log.debug("watch listener add failed", exc_info=True)

    def remove_service(self, zc, type, name):
        with self.lock:
            ip = self.names.pop(name, None)
            if ip is not None:
                log.debug("watch: %s no longer advertising", ip)
                self.state[ip] = False

    def update_service(self, zc, type, name):
        self.add_service(zc, type, name)


def watch_cycle(targets, state, connected_cache, skip_pair=False,
                manual=False, on_connect=None, qr_mode="auto"):
    """One pass over targets: (re)connect everything that is down."""
    results = {}
    for ip in targets:
        if state.get(ip) is False:
            log.debug("watch: %s not advertising, skipping", ip)
            continue
        port = is_device_connected(ip)
        if port:
            connected_cache.add(ip)
            results[ip] = f"{ip}:{port}"
            continue
        connected_cache.discard(ip)
        # Phone advertising the connect service is already paired
        effective_skip_pair = skip_pair or state.get(ip) is True
        target = connect_single_device(ip, skip_pair=effective_skip_pair,
                                       manual=manual, qr_mode=qr_mode,
                                       on_connect=on_connect)
        results[ip] = target
        if target:
            connected_cache.add(ip)
        elif state.get(ip) is True and not skip_pair:
            # Silent (already-paired) attempt failed -> offer the
            # pairing flow on the next cycle
            state[ip] = "pair"
    return results


def _start_watch_browsers(listener):
    """Start zeroconf browsers feeding the watch listener.
    Returns (zc, browsers); (None, []) when zeroconf is unavailable."""
    if Zeroconf is None:
        log.debug("zeroconf unavailable; watch runs without mDNS events")
        return None, []
    zc = Zeroconf()
    browsers = [
        ServiceBrowser(zc, MDNS_CONNECT_TYPE, listener),
        ServiceBrowser(zc, MDNS_TYPE, listener),
    ]
    return zc, browsers


def _watch_rescan(targets, state):
    """Safety net: re-mark targets that are visible on the network.

    mDNS removal events can be missed, leaving state stuck at False.
    """
    try:
        discovered = discover_all_phones(timeout=3, grace=0.5)
    except Exception:
        return
    for rec in discovered:
        ip = rec[0]  # lenient: records may carry extra slots
        if ip in targets and state.get(ip) is False:
            log.debug("watch rescan: %s is back on the network", ip)
            state[ip] = True


def watch_loop(targets, skip_pair=False, manual=False, on_connect=None,
               qr_mode="auto", poll_interval=30, max_cycles=None,
               rescan=True):
    """Watch devices indefinitely: reconnect whenever one (re)appears.

    The --on-connect hook runs on every successful (re)connect.
    Ctrl+C exits cleanly.
    """
    state = {}
    connected_cache = set()
    wake = threading.Event()
    listener = WatchListener(state, wake=wake)
    try:
        zc, browsers = _start_watch_browsers(listener)
    except Exception:
        log.debug("failed to start watch browsers", exc_info=True)
        zc, browsers = None, []

    print(f"\n  Watching {len(targets)} device(s) — Ctrl+C to stop")
    results = {}
    cycle = 0
    woke = False
    try:
        while True:
            cycle += 1
            # Safety-net rescan runs on timer cycles only; event-woken
            # cycles already know the device is back on the network
            if rescan and cycle > 1 and not woke:
                _watch_rescan(targets, state)
            results = watch_cycle(targets, state, connected_cache,
                                  skip_pair=skip_pair, manual=manual,
                                  on_connect=on_connect, qr_mode=qr_mode)
            if max_cycles is not None and cycle >= max_cycles:
                return results
            # Event-driven wake: a service appearing ends the wait early
            woke = wake.wait(poll_interval)
            wake.clear()
    except KeyboardInterrupt:
        print("\n\n  Stopped watching.")
        return results
    finally:
        for browser in browsers:
            try:
                browser.cancel()
            except Exception:
                pass
        if zc is not None:
            try:
                zc.close()
            except Exception:
                pass
