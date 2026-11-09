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
