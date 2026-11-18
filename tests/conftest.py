"""Shared test guards.

PairingSession's listener keeps calling flow._browse for as long as its
session lives — far longer than the patch any single test installs, and
sessions started deep inside connect_single_device/main are not
reachable to stop(). This session-wide default catches those escaped
ticks so no test ever reaches the real network: a silent, blocking
tick. Tests that need listener behavior patch flow._browse locally on
top of it (inner patches restore to this default).
"""
import time
from unittest.mock import patch

import pytest


def _silent_tick(types, timeout, on_info, grace=None):
    """Empty browse tick: block like the real thing, find nothing."""
    time.sleep(timeout)


@pytest.fixture(scope="session", autouse=True)
def guard_flow_browse():
    with patch("adb_wifi_connect.flow._browse", side_effect=_silent_tick):
        yield


_real_run = __import__("subprocess").run


def _no_real_adb(argv, *args, **kwargs):
    """Any adb call that a test did not mock sees a machine with no phones
    (empty `adb devices` / `adb mdns services`, nonzero for the rest) —
    real phones on the developer's network must never leak into results.
    Non-adb commands (e.g. --on-connect hooks) run for real."""
    import subprocess
    from adb_wifi_connect import adb
    if isinstance(argv, (list, tuple)) and argv and argv[0] == adb.ADB_PATH:
        listing = {"devices": "List of devices attached\n\n",
                   "mdns": "List of discovered mdns services\n"}
        out = listing.get(argv[1] if len(argv) > 1 else "", "")
        return subprocess.CompletedProcess(argv, 0 if out else 1, out, "")
    return _real_run(argv, *args, **kwargs)


@pytest.fixture(autouse=True)
def guard_real_adb():
    """Per-test default; a test's own patch of subprocess.run wins."""
    with patch("adb_wifi_connect.adb.subprocess.run", side_effect=_no_real_adb):
        yield
