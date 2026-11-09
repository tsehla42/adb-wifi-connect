import queue
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest


# --- pairing session: QR rendered immediately, scan captured ---

def test_start_pairing_session_renders_qr_immediately():
    from adb_wifi_connect.flow import start_pairing_session
    with patch('adb_wifi_connect.flow.display_qr') as mock_qr, \
         patch('adb_wifi_connect.flow._browse', side_effect=_silent_tick):
        session = start_pairing_session(qr_mode="ascii")
        _stop_listener(session)
    mock_qr.assert_called_once()
    assert session.pair_code
    assert not session.done.is_set()
    assert session.result is None
    # new session contract: an event queue + stop(), no consumed flag
    assert session.events.empty()
    assert callable(session.stop)
    assert not hasattr(session, "consumed")


def test_session_listener_stores_scan_result(capsys):
    """A scan lands on the queue AND fills result/done for the legacy
    wait_for_pairing(session.done) consumers; the listener stays silent."""
    from adb_wifi_connect.flow import start_pairing_session
    fake = _scripted_browse(pairing=[_svc_info("1.2.3.4", 37000, "pairing")],
                            connect=[_svc_info("1.2.3.4", 40000, "connect")])
    with patch('adb_wifi_connect.flow.display_qr'), \
         patch('adb_wifi_connect.flow._browse', side_effect=fake), \
         patch('adb_wifi_connect.flow.run_adb_pair', return_value=True), \
         patch('adb_wifi_connect.flow.run_adb_connect', return_value=True), \
         patch('adb_wifi_connect.adb.read_identity', return_value=None):
        session = start_pairing_session(qr_mode="ascii")
        assert session.done.wait(timeout=3)
        _stop_listener(session)
    assert session.result == ("1.2.3.4", 37000)
    out = capsys.readouterr().out
    assert "Listening for pairing service" not in out, \
        "the session listener must be silent (no banner)"


# --- session-long scan listener: event queue, retry, stop ---

def _svc_info(ip, port, kind):
    """A fake zeroconf ServiceInfo for a pairing/connect service record."""
    return SimpleNamespace(server=ip, port=port,
                           name=f"adb-PHONE01._adb-tls-{kind}._tcp.local.")


def _silent_tick(types, timeout, on_info, grace=None):
    """A flow._browse stand-in: block briefly like a real empty tick,
    find nothing (capped so tests can join the listener fast)."""
    time.sleep(min(timeout, 0.05))


def _stop_listener(session):
    """stop() + bounded join, run INSIDE the patch context so the
    listener cannot make one browse call after the mock is removed."""
    session.stop()
    deadline = time.time() + 3.0
    while session._thread.is_alive() and time.time() < deadline:
        time.sleep(0.02)
    assert not session._thread.is_alive(), "listener failed to stop"


def _scripted_browse(pairing=(), connect=()):
    """A flow._browse double: pairing ticks deliver the scripted infos in
    order (then sleep like a real empty tick); the connect browse delivers
    its own scripted infos. Blocking keeps the listener off a hot loop."""
    pairing = list(pairing)
    connect = list(connect)

    def browse(types, timeout, on_info, grace=None):
        from adb_wifi_connect.mdns import MDNS_TYPE
        if MDNS_TYPE in types:
            if pairing:
                on_info(MDNS_TYPE, pairing.pop(0))
            else:
                time.sleep(min(timeout, 0.05))
            return
        for info in connect:
            if on_info(types[0], info):
                break
    return browse


def test_session_scan_queues_scanned_event(capsys):
    """One scan -> pair, connect, read identity, queue a full 'scanned'
    event with every payload key, and record result/done."""
    from adb_wifi_connect.flow import start_pairing_session

    pairing_info = _svc_info("1.2.3.9", 37000, "pairing")
    connect_info = _svc_info("1.2.3.9", 40000, "connect")
    identity = {"marketname": "Pixel 9", "brand": "google",
                "model": "Pixel 9", "device_name": "my_pixel"}
    fake = _scripted_browse(pairing=[pairing_info], connect=[connect_info])

    with patch('adb_wifi_connect.flow.display_qr'), \
         patch('adb_wifi_connect.flow._browse', side_effect=fake), \
         patch('adb_wifi_connect.flow.run_adb_pair',
               return_value=True) as mock_pair, \
         patch('adb_wifi_connect.flow.run_adb_connect',
               return_value=True) as mock_connect, \
         patch('adb_wifi_connect.adb.read_identity',
               return_value=identity) as mock_ident:
        session = start_pairing_session(qr_mode="ascii")
        try:
            event = session.events.get(timeout=3)
        finally:
            _stop_listener(session)

    assert event == {
        "kind": "scanned",
        "ip": "1.2.3.9",
        "short": "PHONE01",
        "port": 40000,
        "target": "1.2.3.9:40000",
        "identity": identity,
    }
    mock_pair.assert_called_once_with("1.2.3.9", 37000, session.pair_code)
    mock_connect.assert_called_once_with("1.2.3.9", 40000, echo=False)
    mock_ident.assert_called_once_with("1.2.3.9:40000")
    assert session.result == ("1.2.3.9", 37000)
    assert session.done.is_set()
    out = capsys.readouterr().out
    assert "Listening for pairing service" not in out, \
        "the session listener must be silent (no banner)"


def test_session_scan_failure_event_allows_retry():
    """A failed pairing queues {'kind': 'scan-failed', 'ip': ...} and the
    listener keeps running — the very next scan still lands 'scanned'."""
    from adb_wifi_connect.flow import start_pairing_session

    pairing_a = _svc_info("1.2.3.9", 37000, "pairing")
    pairing_b = _svc_info("1.2.3.9", 37001, "pairing")
    connect_b = _svc_info("1.2.3.9", 40000, "connect")
    fake = _scripted_browse(pairing=[pairing_a, pairing_b],
                            connect=[connect_b])

    with patch('adb_wifi_connect.flow.display_qr'), \
         patch('adb_wifi_connect.flow._browse', side_effect=fake), \
         patch('adb_wifi_connect.flow.run_adb_pair',
               side_effect=[False, True]) as mock_pair, \
         patch('adb_wifi_connect.flow.run_adb_connect', return_value=True), \
         patch('adb_wifi_connect.adb.read_identity', return_value=None):
        session = start_pairing_session(qr_mode="ascii")
        try:
            first = session.events.get(timeout=3)
            second = session.events.get(timeout=3)
            assert session._thread.is_alive(), \
                "listener must survive a failed scan (retry-capable)"
        finally:
            _stop_listener(session)

    assert first == {"kind": "scan-failed", "ip": "1.2.3.9"}
    assert second["kind"] == "scanned"
    assert second["ip"] == "1.2.3.9"
    assert second["port"] == 40000
    assert second["target"] == "1.2.3.9:40000"
    assert second["identity"] is None
    assert mock_pair.call_count == 2
    for call in mock_pair.call_args_list:
        assert call.args[2] == session.pair_code, \
            "both pair attempts must use the session's code"


def test_session_stop_ends_listener():
    """stop() is idempotent and the listener exits within one browse tick."""
    from adb_wifi_connect.flow import SCAN_TICK, start_pairing_session

    def slow_browse(types, timeout, on_info, grace=None):
        time.sleep(0.05)  # silent tick: nothing found

    with patch('adb_wifi_connect.flow.display_qr'), \
         patch('adb_wifi_connect.flow._browse', side_effect=slow_browse):
        session = start_pairing_session(qr_mode="ascii")
        session.stop()
        session.stop()  # idempotent
        deadline = time.time() + SCAN_TICK + 1.5
        while session._thread.is_alive() and time.time() < deadline:
            time.sleep(0.05)
        assert not session._thread.is_alive(), \
            "listener must exit within one browse tick of stop()"


# --- zeroconf-absent mode: main-thread hint, guarded listener ---

def test_listener_without_zeroconf_exits_without_browsing():
    """mdns._browse's caller contract: with zeroconf missing the listener
    must not browse at all — it logs once and exits (no retry loop)."""
    from adb_wifi_connect import flow
    with patch.object(flow, "Zeroconf", None), \
         patch.object(flow, "display_qr"), \
         patch.object(flow, "_browse",
                      side_effect=TypeError("NoneType is not callable")
                      ) as mock_browse:
        session = flow.start_pairing_session(qr_mode="ascii")
        try:
            deadline = time.time() + flow.SCAN_TICK + 1.5
            while session._thread.is_alive() and time.time() < deadline:
                time.sleep(0.02)
            assert not session._thread.is_alive(), \
                "listener must exit at once when zeroconf is missing"
            mock_browse.assert_not_called()
        finally:
            session.stop()


def test_start_without_zeroconf_prints_install_hint(capsys):
    """start() runs on the main thread and owns user-facing output: with
    zeroconf missing it prints the old hint, and the QR still renders."""
    from adb_wifi_connect import flow
    with patch.object(flow, "Zeroconf", None), \
         patch.object(flow, "display_qr") as mock_qr:
        session = flow.start_pairing_session(qr_mode="ascii")
        try:
            out = capsys.readouterr().out
        finally:
            _stop_listener(session)
    mock_qr.assert_called_once()
    assert "[zeroconf not installed — QR mode unavailable]" in out, out
    assert "[Install: pip install zeroconf]" in out, out
    assert "Listening for pairing service" not in out, \
        "the listener itself must stay silent (log.debug only)"


# --- result is set on FIRST success (binding contract) ---

def test_session_result_keeps_first_successful_scan():
    """Two successful scans queue two 'scanned' events, but result keeps
    the FIRST scan's (ip, pairing_port) — first-wins, never overwritten."""
    from adb_wifi_connect.flow import start_pairing_session

    pairing_a = _svc_info("1.2.3.4", 37000, "pairing")
    pairing_b = _svc_info("5.6.7.8", 37001, "pairing")
    connect_a = _svc_info("1.2.3.4", 40000, "connect")
    connect_b = _svc_info("5.6.7.8", 40001, "connect")
    fake = _scripted_browse(pairing=[pairing_a, pairing_b],
                            connect=[connect_a, connect_b])

    with patch('adb_wifi_connect.flow.display_qr'), \
         patch('adb_wifi_connect.flow._browse', side_effect=fake), \
         patch('adb_wifi_connect.flow.run_adb_pair', return_value=True), \
         patch('adb_wifi_connect.flow.run_adb_connect', return_value=True), \
         patch('adb_wifi_connect.adb.read_identity', return_value=None):
        session = start_pairing_session(qr_mode="ascii")
        try:
            first = session.events.get(timeout=3)
            second = session.events.get(timeout=3)
            # join INSIDE the patches: scan 2's result assignment happens
            # before the loop re-checks _stop, so a finished thread means
            # both scans fully completed (no sleep-as-assertion)
            _stop_listener(session)
        finally:
            session.stop()
    assert first["kind"] == "scanned" and first["ip"] == "1.2.3.4"
    assert second["kind"] == "scanned" and second["ip"] == "5.6.7.8"
    assert session.done.is_set()
    assert session.result == ("1.2.3.4", 37000), \
        "result is set on first success and must never be overwritten"


# --- lyto-style ordering: QR before discovery ---

def test_main_shows_qr_before_discovery_completes():
    order = []

    def slow_discover(timeout=10, grace=1.0):
        order.append("discovery_started")
        time.sleep(0.3)
        order.append("discovery_done")
        return [("1.2.3.4", "X1", True)]

    def track_qr(*args, **kwargs):
        order.append("qr_displayed")

    with patch('adb_wifi_connect.cli.discover_all_phones',
               side_effect=slow_discover), \
         patch('adb_wifi_connect.flow.display_qr', side_effect=track_qr), \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('adb_wifi_connect.cli.poll_stdin', return_value=True), \
         patch('adb_wifi_connect.cli.connect_single_device',
               return_value="1.2.3.4:5555"), \
         patch('sys.stdin.isatty', return_value=True), \
         patch('sys.argv', ['awc']):
        from adb_wifi_connect.cli import main
        main()

    assert order, "nothing recorded"
    assert order.index("qr_displayed") < order.index("discovery_done"), \
        f"QR must render before discovery returns, got {order}"
    assert order.index("qr_displayed") < order.index("discovery_started"), \
        f"QR must render before discovery even starts, got {order}"


def test_main_warms_adb_early():
    with patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[]), \
         patch('adb_wifi_connect.adb.warm_adb') as mock_warm, \
         patch('sys.argv', ['awc', '--list']):
        from adb_wifi_connect.cli import main
        with pytest.raises(SystemExit):
            main()
    mock_warm.assert_called_once()


def test_main_no_session_for_skip_pair():
    with patch('adb_wifi_connect.cli.start_pairing_session') as mock_start, \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[("1.2.3.4", "X1", True)]), \
         patch('adb_wifi_connect.cli.connect_single_device',
               return_value="1.2.3.4:5555"), \
         patch('sys.argv', ['awc', '--skip-pair']):
        from adb_wifi_connect.cli import main
        main()
    mock_start.assert_not_called()


def test_main_no_session_for_list():
    with patch('adb_wifi_connect.cli.start_pairing_session') as mock_start, \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[("1.2.3.4", "X1", True)]), \
         patch('sys.argv', ['awc', '--list']):
        from adb_wifi_connect.cli import main
        main()
    mock_start.assert_not_called()


def test_main_single_device_passes_session_through():
    session = _event_session()
    session.result = None
    with patch('adb_wifi_connect.cli.start_pairing_session',
               return_value=session) as mock_start, \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[("1.2.3.4", "X1", True)]), \
         patch('adb_wifi_connect.cli.poll_stdin', return_value=True), \
         patch('adb_wifi_connect.cli.connect_single_device',
               return_value="1.2.3.4:5555") as csd, \
         patch('sys.stdin.isatty', return_value=True), \
         patch('sys.argv', ['awc']):
        from adb_wifi_connect.cli import main
        main()
    mock_start.assert_called_once()
    assert csd.call_args.kwargs.get('session') is session


# --- event routing: a queued scan wins before any selection UI ---

def _event_session(events=None):
    """A cli-test session double whose event queue is a REAL queue (the
    drain contract: get_nowait until Empty) — legacy fields stay inert."""
    session = MagicMock()
    session.events = events if events is not None else queue.Queue()
    session.result = None
    return session


def _scanned_event(ip="9.9.9.9", port=41234, short="PIXEL"):
    """A full 'scanned' payload exactly as the listener queues it."""
    return {"kind": "scanned", "ip": ip, "short": short, "port": port,
            "target": f"{ip}:{port}",
            "identity": {"brand": "google", "model": "Pixel 9"}}


def test_scanned_event_before_selection_connects_and_exits(capsys):
    """A scan already queued after discovery: that phone is the target —
    verified, announced, returned — and the selection UI never shows."""
    events = queue.Queue()
    events.put(_scanned_event())
    session = _event_session(events)
    with patch('adb_wifi_connect.cli.start_pairing_session',
               return_value=session), \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[("1.2.3.4", "X1", True, True, 40000, None),
                             ("1.2.3.5", "X2", False, True, 40001, None)]), \
         patch('adb_wifi_connect.cli.enrich_devices', side_effect=list), \
         patch('adb_wifi_connect.cli.select_devices') as mock_select, \
         patch('adb_wifi_connect.cli.connect_single_device') as mock_connect, \
         patch('adb_wifi_connect.adb.is_device_connected',
               return_value=41234), \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('sys.stdin.isatty', return_value=True), \
         patch('sys.argv', ['awc']):
        from adb_wifi_connect.cli import main
        result = main()
    assert result == "9.9.9.9:41234"
    mock_select.assert_not_called()
    mock_connect.assert_not_called()
    out = capsys.readouterr().out
    assert "Connected: 9.9.9.9:41234" in out, out


def test_queued_scan_beats_eager_connect(capsys):
    """A scan queued after discovery terminates the session with that
    phone BEFORE the single-device branch — eager connect must never
    preempt `_early_scan_target`."""
    events = queue.Queue()
    events.put(_scanned_event())
    session = _event_session(events)
    rec = ("1.2.3.4", "X1", True, True, 41234, None)
    with patch('adb_wifi_connect.cli.start_pairing_session',
               return_value=session), \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[rec]), \
         patch('adb_wifi_connect.cli.enrich_devices', side_effect=list), \
         patch('adb_wifi_connect.cli.connect_single_device') as mock_connect, \
         patch('adb_wifi_connect.adb.is_device_connected',
               return_value=41234), \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('sys.stdin.isatty', return_value=True), \
         patch('sys.argv', ['awc']):
        from adb_wifi_connect.cli import main
        result = main()
    assert result == "9.9.9.9:41234"
    mock_connect.assert_not_called()
    out = capsys.readouterr().out
    assert "Found 1 device:" not in out, out   # returned before the branch
    assert "Press Enter to connect" not in out, out
    assert "Connected: 9.9.9.9:41234" in out, out


def test_single_device_prompt_enter_connects_without_qr_wait(capsys):
    """--wait keeps the prompt: a ready record still shows one prompt
    block, then waits for Enter — the record's own
    paired/short_name/connect_port travel to connect_single_device,
    which never opens a QR wait (Task 8)."""
    rec = ("1.2.3.4", "X1", True, True, 41234,
           {"brand": "google", "model": "Pixel 9"})
    session = _event_session()
    with patch('adb_wifi_connect.cli.start_pairing_session',
               return_value=session), \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[rec]), \
         patch('adb_wifi_connect.cli.enrich_devices', side_effect=list), \
         patch('adb_wifi_connect.cli.poll_stdin',
               return_value=True) as mock_poll, \
         patch('adb_wifi_connect.cli.connect_single_device',
               return_value="1.2.3.4:41234") as mock_connect, \
         patch('adb_wifi_connect.flow.wait_for_scanned_event') as mock_wait, \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('sys.stdin.isatty', return_value=True), \
         patch('sys.argv', ['awc', '--wait']):
        from adb_wifi_connect.cli import main
        main()
    kwargs = mock_connect.call_args.kwargs
    assert kwargs.get("paired") is True          # rec[3]
    assert kwargs.get("short_name") == "X1"      # rec[1]
    assert kwargs.get("connect_port") == 41234   # rec[4]
    assert kwargs.get("session") is session
    assert kwargs.get("qr_mode") == "ascii"      # **ckw still threads through
    assert mock_poll.call_count == 1, "waited for exactly one Enter"
    mock_wait.assert_not_called()  # Task 8: no QR wait in this flow
    out = capsys.readouterr().out
    assert "Found 1 device:" in out, out
    assert "1.2.3.4 X1" in out, out          # format_label row
    assert "Press Enter to connect" in out, out
    assert "scan the QR to pair another phone" in out, out
    assert "\nConnected: 1.2.3.4:41234" in out, out


def test_single_device_ready_connects_eagerly_without_prompt(capsys):
    """TTY, no --wait, one READY record (connected/paired): connect
    immediately — no prompt, no Enter poll — with the session/record
    kwargs threading through exactly like the post-Enter call did."""
    rec = ("1.2.3.4", "X1", True, True, 41234,
           {"brand": "google", "model": "Pixel 9"})
    session = _event_session()
    with patch('adb_wifi_connect.cli.start_pairing_session',
               return_value=session) as mock_start, \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[rec]), \
         patch('adb_wifi_connect.cli.enrich_devices', side_effect=list), \
         patch('adb_wifi_connect.cli.poll_stdin',
               return_value=True) as mock_poll, \
         patch('adb_wifi_connect.cli.connect_single_device',
               return_value="1.2.3.4:41234") as mock_connect, \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('sys.stdin.isatty', return_value=True), \
         patch('sys.argv', ['awc']):
        from adb_wifi_connect.cli import main
        result = main()
    assert result == "1.2.3.4:41234"
    mock_start.assert_called_once()
    mock_poll.assert_not_called()          # no prompt loop at all
    mock_connect.assert_called_once()
    kwargs = mock_connect.call_args.kwargs
    assert kwargs.get("session") is session
    assert kwargs.get("paired") is True
    assert kwargs.get("short_name") == "X1"
    assert kwargs.get("connect_port") == 41234
    out = capsys.readouterr().out
    assert "Found 1 device:" in out, out
    assert "Press Enter to connect" not in out, out
    assert "\nConnected: 1.2.3.4:41234" in out, out


def test_single_device_eager_failure_falls_back_to_prompt(capsys):
    """A failed eager connect is never worse than today: hint line,
    then the normal prompt — Enter retries the connect."""
    rec = ("1.2.3.4", "X1", True, True, 41234, None)
    session = _event_session()
    with patch('adb_wifi_connect.cli.start_pairing_session',
               return_value=session), \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[rec]), \
         patch('adb_wifi_connect.cli.enrich_devices', side_effect=list), \
         patch('adb_wifi_connect.cli.poll_stdin', return_value=True), \
         patch('adb_wifi_connect.cli.connect_single_device',
               side_effect=[None, "1.2.3.4:41234"]) as mock_connect, \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('sys.stdin.isatty', return_value=True), \
         patch('sys.argv', ['awc']):
        from adb_wifi_connect.cli import main
        result = main()
    assert result == "1.2.3.4:41234"
    assert mock_connect.call_count == 2   # eager attempt + Enter retry
    out = capsys.readouterr().out
    assert "Could not connect to 1.2.3.4 — press Enter to retry" in out, out
    assert "Press Enter to connect" in out, out
    assert "\nConnected: 1.2.3.4:41234" in out, out


def test_single_device_prompt_hint_omits_qr_clause_without_session(capsys):
    """skip-pair means no QR is on screen: the Enter hint must not
    advertise scanning one."""
    rec = ("1.2.3.4", "X1", False, False, None, None)
    with patch('adb_wifi_connect.cli.start_pairing_session') as mock_start, \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[rec]), \
         patch('adb_wifi_connect.cli.poll_stdin', return_value=True), \
         patch('adb_wifi_connect.cli.connect_single_device',
               return_value="1.2.3.4:5555"), \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('sys.stdin.isatty', return_value=True), \
         patch('sys.argv', ['awc', '--skip-pair']):
        from adb_wifi_connect.cli import main
        main()
    mock_start.assert_not_called()
    out = capsys.readouterr().out
    assert "Press Enter to connect · Ctrl+C quit" in out, out
    assert "scan the QR" not in out, out


def test_single_device_unpaired_record_keeps_prompt(capsys):
    """Nothing to dial (no connect record, no adb row): eager must not
    fire — the QR prompt with its scan clause is the only way forward."""
    rec = ("1.2.3.4", "X1", False, False, None, None)
    session = _event_session()
    with patch('adb_wifi_connect.cli.start_pairing_session',
               return_value=session), \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[rec]), \
         patch('adb_wifi_connect.cli.enrich_devices', side_effect=list), \
         patch('adb_wifi_connect.cli.poll_stdin',
               return_value=True) as mock_poll, \
         patch('adb_wifi_connect.cli.connect_single_device',
               return_value="1.2.3.4:5555") as mock_connect, \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('sys.stdin.isatty', return_value=True), \
         patch('sys.argv', ['awc']):
        from adb_wifi_connect.cli import main
        result = main()
    assert result == "1.2.3.4:5555"
    mock_poll.assert_called_once()        # today's prompt loop
    mock_connect.assert_called_once()
    assert mock_connect.call_args.kwargs.get("paired") is False
    out = capsys.readouterr().out
    assert "Press Enter to connect · scan the QR to pair another phone" \
        in out, out


# --- connect_single_device with a provided session ---

def test_connect_single_device_uses_session_without_rerender():
    """A provided session is reused as-is: no fresh QR, no re-render —
    and since its LISTENER already paired the queued scan (pairing owns
    exactly one place now), flow never pairs again: the target comes
    from the event payload and the connection phase runs on it."""
    from adb_wifi_connect.flow import connect_single_device
    events = queue.Queue()
    session = _event_session(events)
    events.put(_scanned_event("1.2.3.4", 40000, "PHONEA"))

    with patch('adb_wifi_connect.flow.is_device_connected',
               side_effect=[None, True]), \
         patch('adb_wifi_connect.flow.display_qr') as mock_qr, \
         patch('adb_wifi_connect.flow.start_pairing_session') as mock_start, \
         patch('adb_wifi_connect.flow.run_adb_pair') as mock_pair, \
         patch('adb_wifi_connect.adb.connected_serials', return_value=set()), \
         patch('adb_wifi_connect.flow.connect_with_retries',
               return_value=True), \
         patch('sys.stdin.isatty', return_value=True):
        result = connect_single_device("1.2.3.4", skip_pair=False,
                                       manual=False, session=session)
    assert result == "1.2.3.4:40000"
    mock_qr.assert_not_called()       # never re-rendered
    mock_start.assert_not_called()    # no fresh session created
    mock_pair.assert_not_called()     # listener paired — flow must not


def test_connect_single_device_reuses_session_but_never_replays_stale_scan(
        capsys):
    """Reuse semantics: the session (QR, credentials, listener) serves
    every wait, but EVENTS ARE CONSUMED ONCE. A scan an earlier wait
    already took (queue empty, `done` still set, `result` still filled)
    must NOT hijack the next target: the second wait BLOCKS for a new
    event, and each wait returns its own payload's target."""
    from adb_wifi_connect.flow import connect_single_device
    events = queue.Queue()
    session = _event_session(events)
    session.done = threading.Event()
    session.done.set()                    # stale session state from scan A
    session.result = ("1.2.3.4", 37000)   # ...and its legacy result
    events.put(_scanned_event("1.2.3.4", 40000, "PHONEA"))
    scan_b = _scanned_event("1.2.3.5", 40001, "PHONEB")
    polls = {"n": 0}

    def scan_during_second_wait(timeout):
        # scan B lands only WHILE the second wait is polling
        polls["n"] += 1
        events.put(scan_b)
        return False

    with patch('adb_wifi_connect.flow.is_device_connected',
               side_effect=[None, True, None, True]), \
         patch('adb_wifi_connect.flow.display_qr') as mock_qr, \
         patch('adb_wifi_connect.flow.start_pairing_session') as mock_start, \
         patch('adb_wifi_connect.flow.run_adb_pair') as mock_pair, \
         patch('adb_wifi_connect.flow.poll_stdin',
               side_effect=scan_during_second_wait, create=True) as mock_poll, \
         patch('adb_wifi_connect.flow.discover_connection_port',
               return_value=(None, None)), \
         patch('adb_wifi_connect.flow.scan_for_adb_port', return_value=5555), \
         patch('adb_wifi_connect.adb.connected_serials', return_value=set()), \
         patch('adb_wifi_connect.flow.connect_with_retries',
               return_value=True), \
         patch('sys.stdin.isatty', return_value=True):
        first = connect_single_device("1.2.3.4", skip_pair=False,
                                      manual=False, session=session)
        second = connect_single_device("1.2.3.5", skip_pair=False,
                                       manual=False, session=session)
    assert first == "1.2.3.4:40000"     # its own payload
    assert second == "1.2.3.5:40001"    # NOT the stale 1.2.3.4 again
    assert mock_poll.call_count >= 1, \
        "second wait must block on the queue, not on the sticky done"
    mock_start.assert_not_called()      # reuse: never a fresh QR
    mock_qr.assert_not_called()         # never re-rendered
    mock_pair.assert_not_called()       # listener pairs; flow never does
    out = capsys.readouterr().out
    assert out.count("Connected: 1.2.3.4:40000") == 1, out
    assert out.count("Connected: 1.2.3.5:40001") == 1, out


def test_connect_single_device_scan_lands_during_wait_returns_event_target(
        capsys):
    """The scan-arriving-mid-wait path: the queue is EMPTY when the wait
    starts, a scan lands while it polls — the wait ends with THAT
    event's payload (the listener already paired + connected), so flow
    completes without re-pairing and without any manual prompt, with
    exactly one status line."""
    from adb_wifi_connect.flow import connect_single_device
    events = queue.Queue()
    session = _event_session(events)
    event = _scanned_event("5.5.5.5", 41999, "PIXELX")

    def scan_lands(timeout):
        events.put(event)
        return False

    with patch('adb_wifi_connect.flow.is_device_connected',
               side_effect=[None, True]), \
         patch('adb_wifi_connect.flow.poll_stdin',
               side_effect=scan_lands, create=True) as mock_poll, \
         patch('adb_wifi_connect.flow.run_adb_pair') as mock_pair, \
         patch('adb_wifi_connect.flow.ask_pairing_code') as mock_code, \
         patch('adb_wifi_connect.adb.connected_serials', return_value=set()), \
         patch('adb_wifi_connect.flow.connect_with_retries',
               return_value=True), \
         patch('sys.stdin.isatty', return_value=True):
        result = connect_single_device("1.2.3.4", skip_pair=False,
                                       manual=False, session=session)
    assert result == "5.5.5.5:41999"
    assert mock_poll.call_count >= 1, "the scan landed while waiting"
    mock_pair.assert_not_called()       # listener owns pairing
    mock_code.assert_not_called()       # no manual prompt
    out = capsys.readouterr().out
    assert "Scanned phone 5.5.5.5 selected instead of 1.2.3.4" in out, out
    assert "Switching to manual mode" not in out, out
    assert "QR pairing failed" not in out, out
    assert out.count("Connected: 5.5.5.5:41999") == 1, out


def test_connect_single_device_wait_ignores_stale_done_blocks_for_new_event(
        capsys):
    """Sticky guard: `done`/`result` stay set for the whole session after
    the FIRST scan — they must never satisfy a later wait. Queue empty
    at wait start (the earlier scan was already consumed) → the wait
    blocks for a NEW event, and only that new payload becomes the
    target."""
    from adb_wifi_connect.flow import connect_single_device
    events = queue.Queue()
    session = _event_session(events)
    session.done = threading.Event()
    session.done.set()                    # stale: an earlier scan set it
    session.result = ("9.9.9.9", 37000)   # stale: its result
    fresh = _scanned_event("5.5.5.5", 42001, "PIXELNEW")
    polls = {"n": 0}

    def poll_then_scan(timeout):
        polls["n"] += 1
        if polls["n"] == 1:
            return False                  # still nothing: keep blocking
        events.put(fresh)                 # the NEW scan lands later
        return False

    with patch('adb_wifi_connect.flow.is_device_connected',
               side_effect=[None, True]), \
         patch('adb_wifi_connect.flow.poll_stdin',
               side_effect=poll_then_scan, create=True) as mock_poll, \
         patch('adb_wifi_connect.flow.run_adb_pair') as mock_pair, \
         patch('adb_wifi_connect.adb.connected_serials', return_value=set()), \
         patch('adb_wifi_connect.flow.connect_with_retries',
               return_value=True), \
         patch('sys.stdin.isatty', return_value=True):
        result = connect_single_device("1.2.3.4", skip_pair=False,
                                       manual=False, session=session)
    assert mock_poll.call_count >= 2, \
        "wait returned before a NEW event arrived (sticky done satisfied it)"
    assert result == "5.5.5.5:42001"
    mock_pair.assert_not_called()
    out = capsys.readouterr().out
    assert "9.9.9.9" not in out, out


def test_connect_single_device_scan_failed_prints_retry_and_keeps_waiting(
        capsys):
    """A 'scan-failed' payload during the wait prints its retry line and
    the wait CONTINUES — the still-running listener serves the rescan
    that follows."""
    from adb_wifi_connect.flow import connect_single_device
    events = queue.Queue()
    session = _event_session(events)
    events.put({"kind": "scan-failed", "ip": "1.2.3.4"})
    events.put(_scanned_event("1.2.3.4", 40000, "PHONEA"))

    with patch('adb_wifi_connect.flow.is_device_connected',
               side_effect=[None, True]), \
         patch('adb_wifi_connect.flow.run_adb_pair') as mock_pair, \
         patch('adb_wifi_connect.adb.connected_serials', return_value=set()), \
         patch('adb_wifi_connect.flow.connect_with_retries',
               return_value=True), \
         patch('sys.stdin.isatty', return_value=True):
        result = connect_single_device("1.2.3.4", skip_pair=False,
                                       manual=False, session=session)
    assert result == "1.2.3.4:40000"
    mock_pair.assert_not_called()
    out = capsys.readouterr().out
    assert out.count("  Pairing failed for 1.2.3.4 — scan again") == 1, out


def test_connect_single_device_dead_scan_transport_retries_then_serves_rescan(
        capsys):
    """Transport verification mirrors cli's wait sites: a queued scan
    whose transport does not answer (verify → one quiet reconnect retry →
    re-verify) prints the dead-transport retry line and the wait
    CONTINUES — the next verified scan completes the run."""
    from adb_wifi_connect.flow import connect_single_device
    events = queue.Queue()
    session = _event_session(events)
    events.put(_scanned_event("1.2.3.4", 40000, "PHONEA"))   # dead
    alive = _scanned_event("1.2.3.4", 40001, "PHONEA")       # rescan

    def rescan(timeout):
        events.put(alive)
        return False

    with patch('adb_wifi_connect.flow.is_device_connected',
               side_effect=[None, False, False, True]), \
         patch('adb_wifi_connect.flow.run_adb_connect',
               return_value=True) as mock_connect, \
         patch('adb_wifi_connect.flow.poll_stdin',
               side_effect=rescan, create=True), \
         patch('adb_wifi_connect.flow.run_adb_pair') as mock_pair, \
         patch('adb_wifi_connect.adb.connected_serials', return_value=set()), \
         patch('adb_wifi_connect.flow.connect_with_retries',
               return_value=True), \
         patch('sys.stdin.isatty', return_value=True):
        result = connect_single_device("1.2.3.4", skip_pair=False,
                                       manual=False, session=session)
    assert result == "1.2.3.4:40001"
    mock_pair.assert_not_called()
    # the quiet reconnect retry (echo=False) fired for the dead scan only
    mock_connect.assert_called_once_with("1.2.3.4", 40000, echo=False)
    out = capsys.readouterr().out
    assert ("  Scan paired 1.2.3.4 but connect failed — scan again or "
            "check Wireless debugging") in out, out
    assert out.count("Connected: 1.2.3.4:40001") == 1, out


def test_connect_single_device_wait_at_eof_stops_polling_still_ends_on_scan():
    """stdin at EOF during the wait: poll_stdin would report ready
    FOREVER — the wait must notice once (readline → '') and pace its
    drains with sleep instead of spinning, while a later scan still
    ends it."""
    from adb_wifi_connect.flow import connect_single_device
    events = queue.Queue()
    session = _event_session(events)
    event = _scanned_event("6.6.6.6", 42002, "PIXELEOF")
    fake_stdin = MagicMock()
    fake_stdin.isatty.return_value = True
    fake_stdin.readline.return_value = ""     # EOF: nothing to read
    polls = {"n": 0}
    sleeps = {"n": 0}

    def eof_poll(timeout):
        polls["n"] += 1
        if polls["n"] > 1:
            raise AssertionError(
                f"EOF stdin polled {polls['n']} times — busy-spin")
        return True                          # EOF reads as ready

    def pace_then_scan(seconds):
        sleeps["n"] += 1
        events.put(event)                    # scan lands while paced

    with patch('adb_wifi_connect.flow.is_device_connected',
               side_effect=[None, True]), \
         patch('adb_wifi_connect.flow.poll_stdin',
               side_effect=eof_poll, create=True), \
         patch('adb_wifi_connect.flow.time.sleep',
               side_effect=pace_then_scan), \
         patch('adb_wifi_connect.flow.sys.stdin', fake_stdin), \
         patch('adb_wifi_connect.flow.run_adb_pair') as mock_pair, \
         patch('adb_wifi_connect.adb.connected_serials', return_value=set()), \
         patch('adb_wifi_connect.flow.connect_with_retries',
               return_value=True):
        result = connect_single_device("1.2.3.4", skip_pair=False,
                                       manual=False, session=session)
    assert result == "6.6.6.6:42002"
    assert polls["n"] == 1, "EOF stdin must not be re-polled"
    assert fake_stdin.readline.call_count == 1
    assert sleeps["n"] >= 1, "drains continued while paced by sleep"
    mock_pair.assert_not_called()


# --- adb warm-up ---

def test_warm_adb_runs_start_server_and_survives_missing_adb():
    from adb_wifi_connect.adb import warm_adb
    with patch('adb_wifi_connect.adb.subprocess.run',
               side_effect=FileNotFoundError("adb missing")):
        warm_adb()  # must not raise
    with patch('adb_wifi_connect.adb.subprocess.run') as mock_run:
        warm_adb()
        deadline = time.time() + 2
        while time.time() < deadline and not mock_run.called:
            time.sleep(0.05)
    mock_run.assert_called_once()
    assert mock_run.call_args[0][0][-1] == "start-server"


# --- discovery grace default ---

def test_discover_default_grace_is_one_second():
    import inspect
    from adb_wifi_connect.mdns import discover_all_phones
    default = inspect.signature(discover_all_phones).parameters['grace'].default
    assert default == 1.0, f"grace default is {default}, expected 1.0"


def test_zero_device_wait_ends_on_scan_event(capsys):
    """0 devices + a live QR session: wait for the scan — no countdown,
    no 'waiting up to' window — and end on the verified event."""
    events = queue.Queue()
    session = _event_session(events)
    payload = _scanned_event("7.7.7.7", 41999, "PIXEL0")

    def poll_then_scan(timeout):
        # the scan lands while the wait is polling for a stray Enter
        events.put(payload)
        return False

    with patch('adb_wifi_connect.cli.start_pairing_session',
               return_value=session), \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[]), \
         patch('adb_wifi_connect.cli.poll_stdin',
               side_effect=poll_then_scan) as mock_poll, \
         patch('adb_wifi_connect.adb.is_device_connected',
               return_value=41999), \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('sys.stdin.isatty', return_value=True), \
         patch('sys.argv', ['awc']):
        from adb_wifi_connect.cli import main
        result = main()
    assert result == "7.7.7.7:41999"
    assert mock_poll.call_args.args[0] == 0.5, "bounded Enter poll"
    out = capsys.readouterr().out
    assert "No devices found — scan the QR to pair a phone" in out, out
    assert "waiting up to" not in out, out
    assert "for the QR scan" not in out, out  # remainder-window countdown


def test_zero_device_wait_at_eof_stops_polling_still_ends_on_event():
    """stdin at EOF (closed pipe, `< /dev/null`, exhausted redirect):
    poll_stdin reports it ready instantly and readline() returns '' —
    the 0-device wait must poll stdin at most once, then pace its drains
    with sleep instead of spinning, while a later scan still ends it."""
    events = queue.Queue()
    session = _event_session(events)
    payload = _scanned_event("8.8.8.8", 42000, "PIXELEOF")
    fake_stdin = MagicMock()
    fake_stdin.isatty.return_value = True
    fake_stdin.readline.return_value = ""          # EOF: nothing to read
    polls = {"n": 0}
    sleeps = {"n": 0}

    def eof_poll(timeout):
        polls["n"] += 1
        if polls["n"] > 1:
            raise AssertionError(
                f"stdin at EOF was polled {polls['n']} times — busy-spin")
        return True                                # EOF reads as ready

    def pace_then_scan(seconds):
        sleeps["n"] += 1
        if sleeps["n"] >= 3:                       # scan lands mid-wait
            events.put(payload)

    with patch('adb_wifi_connect.cli.start_pairing_session',
               return_value=session), \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[]), \
         patch('adb_wifi_connect.cli.poll_stdin',
               side_effect=eof_poll) as mock_poll, \
         patch('adb_wifi_connect.cli.time.sleep', side_effect=pace_then_scan), \
         patch('sys.stdin', fake_stdin), \
         patch('adb_wifi_connect.adb.is_device_connected',
               return_value=42000), \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('sys.argv', ['awc']):
        from adb_wifi_connect.cli import main
        result = main()
    assert result == "8.8.8.8:42000"
    assert mock_poll.call_count == 1, "EOF stdin must not be re-polled"
    assert fake_stdin.readline.call_count == 1, "EOF found by the discard read"
    assert sleeps["n"] >= 3, "drains continued while paced by sleep"


# --- session-long listener tick (one number, no scan window) ---

def test_single_listener_tick_constant():
    from adb_wifi_connect.flow import SCAN_TICK
    assert SCAN_TICK == 2


def test_listener_browses_pairing_services_silently(capsys):
    """The session-long listener drives silent pairing ticks through
    _browse — no banner, no announce/timeout kwargs like the old
    one-shot discover_pairing_service."""
    from adb_wifi_connect.flow import SCAN_TICK, start_pairing_session
    from adb_wifi_connect.mdns import MDNS_TYPE
    with patch('adb_wifi_connect.flow.display_qr'), \
         patch('adb_wifi_connect.flow._browse',
               side_effect=_silent_tick) as mock_browse:
        session = start_pairing_session(qr_mode="ascii")
        deadline = time.time() + 2
        while not mock_browse.called and time.time() < deadline:
            time.sleep(0.02)
        _stop_listener(session)
    assert mock_browse.called, "listener never browsed"
    call = mock_browse.call_args
    assert call.args[0] == [MDNS_TYPE]
    assert call.args[1] == SCAN_TICK
    assert callable(call.args[2])
    assert call.kwargs == {}, "silent tick takes no announce/timeout kwargs"
    out = capsys.readouterr().out
    assert "Listening for pairing service" not in out, \
        "the session listener must be silent (no banner)"


def test_listener_tick_follows_scan_tick_constant():
    from adb_wifi_connect.flow import start_pairing_session
    with patch('adb_wifi_connect.flow.SCAN_TICK', 0.05), \
         patch('adb_wifi_connect.flow.display_qr'), \
         patch('adb_wifi_connect.flow._browse',
               side_effect=_silent_tick) as mock_browse:
        session = start_pairing_session(qr_mode="ascii")
        deadline = time.time() + 2
        while not mock_browse.called and time.time() < deadline:
            time.sleep(0.02)
        _stop_listener(session)
    assert mock_browse.called, "listener never browsed"
    assert mock_browse.call_args.args[1] == 0.05


def test_foreground_wait_gets_no_timeout_kwarg():
    """Task 8: the foreground QR wait is timerless — connect_single_device
    must not hand it an explicit timeout (no window constant drives it)."""
    from adb_wifi_connect.flow import connect_single_device
    with patch('adb_wifi_connect.flow.is_device_connected',
               return_value=None), \
         patch('adb_wifi_connect.flow.display_qr'), \
         patch('adb_wifi_connect.flow.wait_for_scanned_event',
               return_value="manual") as mock_wait, \
         patch('adb_wifi_connect.flow.ask_pairing_code',
               return_value=None), \
         patch('adb_wifi_connect.flow.ask_pairing_port',
               return_value=37000), \
         patch('adb_wifi_connect.flow.run_adb_pair',
               return_value=False), \
         patch('sys.stdin.isatty', return_value=True):
        connect_single_device("1.2.3.4", skip_pair=False, manual=False)
    assert mock_wait.call_count == 1
    assert "timeout" not in mock_wait.call_args.kwargs, \
        mock_wait.call_args


def test_foreground_messages_have_no_countdown(capsys):
    """The wait announcement is ONE hint — window values and countdown
    strings never leak into user-facing output."""
    from adb_wifi_connect.flow import connect_single_device
    with patch('adb_wifi_connect.flow.is_device_connected',
               return_value=None), \
         patch('adb_wifi_connect.flow.display_qr'), \
         patch('adb_wifi_connect.flow.wait_for_scanned_event',
               return_value="manual"), \
         patch('adb_wifi_connect.flow.ask_pairing_code',
               return_value=None), \
         patch('adb_wifi_connect.flow.ask_pairing_port',
               return_value=37000), \
         patch('adb_wifi_connect.flow.run_adb_pair',
               return_value=False), \
         patch('sys.stdin.isatty', return_value=True):
        connect_single_device("1.2.3.4", skip_pair=False, manual=False)
    out = capsys.readouterr().out
    assert ("Waiting for the QR scan — press Enter for manual mode, "
            "Ctrl+C to quit") in out, out
    assert "7s" not in out, out
    assert "s timeout" not in out, out
    assert "QR timeout" not in out, out


def test_listening_banner_uses_announce(capsys):
    from adb_wifi_connect.mdns import discover_pairing_service
    with patch('adb_wifi_connect.mdns._browse'):
        discover_pairing_service(timeout=35, announce=30)
    out = capsys.readouterr().out
    assert "(timeout 30s)" in out, out
    assert "35" not in out, "internal browse window leaked into the message"

