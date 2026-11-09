import sys
import os

from unittest.mock import patch, MagicMock
import threading

import adb_wifi_connect.connect
from adb_wifi_connect import adb
from adb_wifi_connect.connect import (
    run_adb_pair,
    run_adb_connect,
    is_device_connected,
    scan_for_adb_port,
)


def test_run_adb_pair_uses_argv_list():
    """run_adb_pair must call subprocess with an argv list, not a shell string."""
    with patch('adb_wifi_connect.adb.ADB_PATH', 'C:/platform-tools/adb.exe'), \
         patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(
            stdout="Successfully paired to 1.2.3.4:37000", returncode=0)
        assert run_adb_pair("1.2.3.4", 37000, "123456") is True
    argv = mock_run.call_args[0][0]
    assert argv == ['C:/platform-tools/adb.exe', 'pair', '1.2.3.4:37000', '123456']
    assert 'shell' not in mock_run.call_args[1]


def test_run_adb_connect_uses_argv_list():
    with patch('adb_wifi_connect.adb.ADB_PATH', 'my-adb'), \
         patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(
            stdout="connected to 1.2.3.4:5555", returncode=0)
        assert run_adb_connect("1.2.3.4", 5555) is True
    assert mock_run.call_args[0][0] == ['my-adb', 'connect', '1.2.3.4:5555']
    assert 'shell' not in mock_run.call_args[1]


def test_is_device_connected_uses_argv_lists():
    with patch('adb_wifi_connect.adb.ADB_PATH', 'adb-custom'), \
         patch('subprocess.run') as mock_run:
        outputs = [
            MagicMock(stdout="List of devices attached\n1.2.3.4:5555 device\n",
                      returncode=0),
            MagicMock(stdout="disconnected 1.2.3.4:5555", returncode=0),
            MagicMock(stdout="connected to 1.2.3.4:5555", returncode=0),
            MagicMock(stdout="test\n", returncode=0),
        ]
        mock_run.side_effect = outputs
        assert is_device_connected("1.2.3.4") == 5555

    assert mock_run.call_count == 4
    for c in mock_run.call_args_list:
        argv = c[0][0]
        assert isinstance(argv, list), f"expected argv list, got {argv!r}"
        assert argv[0] == 'adb-custom'
        assert 'shell' not in c[1]


def test_scan_for_adb_port_uses_argv_lists():
    def fake_run(argv, **kwargs):
        assert isinstance(argv, list), f"expected argv list, got {argv!r}"
        assert argv[0] == 'scan-adb'
        if argv[2] == "1.2.3.4:5555":
            return MagicMock(stdout="connected to 1.2.3.4:5555", returncode=0)
        return MagicMock(stdout="cannot connect to 1.2.3.4:xxxx", returncode=1)

    with patch('adb_wifi_connect.adb.ADB_PATH', 'scan-adb'), \
         patch('subprocess.run', side_effect=fake_run):
        assert scan_for_adb_port("1.2.3.4", timeout=5) == 5555


# --- --debug logging ---

import logging


def test_main_debug_flag_configures_debug_logging():
    devices = [("192.168.1.10", "Pixel", False)]
    with patch('adb_wifi_connect.cli.discover_all_phones', return_value=devices), \
         patch('adb_wifi_connect.cli.connect_single_device', return_value="192.168.1.10:5555"), \
         patch('adb_wifi_connect.cli.logging.basicConfig') as mock_bc, \
         patch('sys.argv', ['awc', '--debug']):
        from adb_wifi_connect.connect import main
        main()
    mock_bc.assert_called_once()
    assert mock_bc.call_args.kwargs.get('level') == logging.DEBUG


def test_run_adb_pair_logs_command_at_debug(caplog):
    with caplog.at_level('DEBUG'), \
         patch('subprocess.run',
               return_value=MagicMock(stdout="Successfully paired", returncode=0)):
        run_adb_pair("1.2.3.4", 37000, "123456")
    messages = [r.getMessage() for r in caplog.records]
    assert any("1.2.3.4:37000" in m for m in messages), messages


# --- wait_for_pairing: Windows-safe QR wait ---

def test_wait_for_pairing_returns_discovered_when_event_set():
    from adb_wifi_connect.connect import wait_for_pairing
    done = threading.Event()
    done.set()
    assert wait_for_pairing(done, timeout=0.2) == "discovered"


def test_wait_for_pairing_enter_switches_to_manual():
    from adb_wifi_connect.connect import wait_for_pairing
    done = threading.Event()
    with patch('select.select', return_value=([sys.stdin], [], [])), \
         patch('sys.stdin.readline', return_value="\n"):
        assert wait_for_pairing(done, timeout=5) == "manual"


def test_wait_for_pairing_timeout():
    from adb_wifi_connect.connect import wait_for_pairing
    done = threading.Event()
    with patch('select.select', return_value=([], [], [])):
        assert wait_for_pairing(done, timeout=0.3) == "timeout"


def test_wait_for_pairing_windows_skips_select():
    from adb_wifi_connect.connect import wait_for_pairing
    done = threading.Event()
    with patch('adb_wifi_connect.ui.os.name', 'nt'), \
         patch('adb_wifi_connect.ui.select.select') as mock_select:
        assert wait_for_pairing(done, timeout=0.3) == "timeout"
    mock_select.assert_not_called()


def test_wait_for_pairing_windows_polls_until_discovered():
    from adb_wifi_connect.connect import wait_for_pairing
    done = threading.Event()
    threading.Timer(0.1, done.set).start()
    with patch('adb_wifi_connect.ui.os.name', 'nt'):
        assert wait_for_pairing(done, timeout=5) == "discovered"


def test_wait_for_pairing_falls_back_when_select_unusable():
    """If select() raises (e.g. Windows stdin), poll instead of aborting."""
    from adb_wifi_connect.connect import wait_for_pairing
    done = threading.Event()
    with patch('adb_wifi_connect.ui.select.select',
               side_effect=OSError("select can't watch stdin")):
        assert wait_for_pairing(done, timeout=0.5) == "timeout"


def test_wait_for_pairing_default_waits_without_timeout():
    """timeout defaults to None: the wait ends only on a scan or on
    Enter — never on a timer, however much time passes."""
    import inspect
    from adb_wifi_connect.connect import wait_for_pairing

    sig = inspect.signature(wait_for_pairing)
    assert sig.parameters["timeout"].default is None

    # Enter path still ends an unset wait: "manual", never "timeout"
    done = threading.Event()
    with patch('select.select', return_value=([sys.stdin], [], [])), \
         patch('sys.stdin.readline', return_value="\n"):
        assert wait_for_pairing(done) == "manual"
    assert not done.is_set()

    # Simulated clock races far past any legacy 30s default — with no
    # timeout set, nothing may expire; the scan event still wins.
    clock = {"now": 1000.0}

    def race_clock():
        clock["now"] += 99999.0
        return clock["now"]

    def fire_scan(_seconds):
        done.set()

    with patch('adb_wifi_connect.ui.time') as mock_time, \
         patch('adb_wifi_connect.ui.os.name', 'nt'):
        mock_time.time.side_effect = race_clock
        mock_time.sleep.side_effect = fire_scan
        assert wait_for_pairing(done) == "discovered"


def test_connect_single_device_qr_enter_switches_to_manual():
    with patch('adb_wifi_connect.flow.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.flow.sys.stdin.isatty', return_value=True), \
         patch('adb_wifi_connect.flow.display_qr'), \
         patch('adb_wifi_connect.flow.wait_for_scanned_event', return_value="manual") as mock_wait, \
         patch('adb_wifi_connect.flow.ask_pairing_code', return_value="123456"), \
         patch('adb_wifi_connect.flow.ask_pairing_port', return_value=37000), \
         patch('adb_wifi_connect.flow.run_adb_pair', return_value=True), \
         patch('adb_wifi_connect.flow.discover_connection_port', return_value=(None, None)), \
         patch('adb_wifi_connect.flow.scan_for_adb_port', return_value=5555):
        from adb_wifi_connect.connect import connect_single_device
        result = connect_single_device("1.2.3.4", manual=False, skip_pair=False)
    mock_wait.assert_called_once()
    assert result == "1.2.3.4:5555"


def test_connect_single_device_qr_scan_event_never_re_pairs(capsys):
    """Ground truth (Task 7): the session's LISTENER pairs, connects and
    queues the 'scanned' payload. connect_single_device takes the target
    from that payload and NEVER pairs again — a successful `adb pair`
    immediately deregisters the pairing service, so a second pair would
    fail against a closed port and force manual prompts behind a false
    "QR pairing failed" message. Pairing owns exactly one place."""
    import queue
    session = MagicMock()
    session.events = queue.Queue()
    session.result = None
    session.events.put({"kind": "scanned", "ip": "1.2.3.4", "short": "PIXEL",
                        "port": 40000, "target": "1.2.3.4:40000",
                        "identity": {"brand": "google", "model": "Pixel 9"}})
    with patch('adb_wifi_connect.flow.is_device_connected',
               side_effect=[None, True]), \
         patch('adb_wifi_connect.flow.sys.stdin.isatty', return_value=True), \
         patch('adb_wifi_connect.flow.display_qr'), \
         patch('adb_wifi_connect.flow.start_pairing_session') as mock_start, \
         patch('adb_wifi_connect.flow.run_adb_pair') as mock_pair, \
         patch('adb_wifi_connect.flow.ask_pairing_code') as mock_code, \
         patch('adb_wifi_connect.adb.connected_serials', return_value=set()), \
         patch('adb_wifi_connect.flow.connect_with_retries',
               return_value=True):
        from adb_wifi_connect.connect import connect_single_device
        result = connect_single_device("1.2.3.4", manual=False,
                                       skip_pair=False, session=session)
    assert result == "1.2.3.4:40000"
    mock_pair.assert_not_called()      # the listener already paired
    mock_code.assert_not_called()      # no forced manual prompts
    mock_start.assert_not_called()     # session reused as-is
    out = capsys.readouterr().out
    assert "QR pairing failed" not in out, out
    assert "Switching to manual mode" not in out, out
    assert out.count("Connected: 1.2.3.4:40000") == 1, out


# --- QR rendering modes (Sixel with ASCII fallback) ---

import pytest


def test_display_qr_auto_falls_back_to_ascii_when_sixel_unsupported():
    from adb_wifi_connect.connect import display_qr
    with patch('adb_wifi_connect.qr.supports_sixel', return_value=False), \
         patch('adb_wifi_connect.qr._display_qr_ascii') as ascii_mock:
        display_qr("WIFI:T:ADB;S:a;P:b;;")
    ascii_mock.assert_called_once_with("WIFI:T:ADB;S:a;P:b;;")


def test_display_qr_auto_uses_sixel_when_supported():
    from adb_wifi_connect.connect import display_qr
    with patch('adb_wifi_connect.qr.supports_sixel', return_value=True), \
         patch('adb_wifi_connect.qr._render_sixel_qr', return_value="\x1bPq-data\x1b\\") as sx, \
         patch('adb_wifi_connect.qr._display_qr_ascii') as ascii_mock, \
         patch('builtins.print'):
        display_qr("WIFI:T:ADB;S:a;P:b;;")
    sx.assert_called_once_with("WIFI:T:ADB;S:a;P:b;;")
    ascii_mock.assert_not_called()


def test_display_qr_auto_falls_back_to_ascii_when_sixel_render_fails():
    """Any Sixel error must degrade silently to ASCII, never crash."""
    from adb_wifi_connect.connect import display_qr
    with patch('adb_wifi_connect.qr.supports_sixel', return_value=True), \
         patch('adb_wifi_connect.qr._render_sixel_qr', side_effect=RuntimeError("boom")), \
         patch('adb_wifi_connect.qr._display_qr_ascii') as ascii_mock:
        display_qr("data")
    ascii_mock.assert_called_once_with("data")


def test_display_qr_ascii_mode_never_touches_sixel():
    from adb_wifi_connect.connect import display_qr
    with patch('adb_wifi_connect.qr.supports_sixel') as sx, \
         patch('adb_wifi_connect.qr._render_sixel_qr') as render, \
         patch('adb_wifi_connect.qr._display_qr_ascii') as ascii_mock:
        display_qr("data", mode="ascii")
    sx.assert_not_called()
    render.assert_not_called()
    ascii_mock.assert_called_once_with("data")


def test_display_qr_forced_sixel_falls_back_with_notice():
    from adb_wifi_connect.connect import display_qr
    with patch('adb_wifi_connect.qr._render_sixel_qr', side_effect=RuntimeError("boom")), \
         patch('adb_wifi_connect.qr._display_qr_ascii') as ascii_mock, \
         patch('builtins.print') as mock_print:
        display_qr("data", mode="sixel")
    ascii_mock.assert_called_once_with("data")
    notices = [str(c) for c in mock_print.call_args_list]
    assert any("ASCII" in n for n in notices), notices


def test_supports_sixel_true_when_da_response_has_attr_4():
    from adb_wifi_connect.connect import supports_sixel
    with patch('adb_wifi_connect.qr._query_device_attrs', return_value="\x1b[?62;4;9c"):
        assert supports_sixel() is True


def test_supports_sixel_false_when_attr_4_missing():
    from adb_wifi_connect.connect import supports_sixel
    with patch('adb_wifi_connect.qr._query_device_attrs', return_value="\x1b[?62;1;9c"):
        assert supports_sixel() is False


def test_supports_sixel_false_when_query_fails():
    from adb_wifi_connect.connect import supports_sixel
    with patch('adb_wifi_connect.qr._query_device_attrs', return_value=None):
        assert supports_sixel() is False


def test_supports_sixel_false_on_windows_without_probe():
    from adb_wifi_connect.connect import supports_sixel
    with patch('adb_wifi_connect.qr.os.name', 'nt'), \
         patch('adb_wifi_connect.qr._query_device_attrs') as query:
        assert supports_sixel() is False
    query.assert_not_called()


def test_query_device_attrs_none_when_not_a_tty():
    from adb_wifi_connect.connect import _query_device_attrs
    with patch.object(sys.stdin, 'isatty', return_value=False):
        assert _query_device_attrs() is None


def test_query_device_attrs_none_on_probe_error():
    from adb_wifi_connect.connect import _query_device_attrs
    with patch.object(sys.stdin, 'isatty', return_value=True), \
         patch.object(sys.stdout, 'isatty', return_value=True), \
         patch.object(sys.stdin, 'fileno', side_effect=OSError("bad fd")):
        assert _query_device_attrs() is None


def test_render_sixel_qr_propagates_import_error():
    """If the sixel extra isn't installed, the ImportError must escape
    so display_qr can fall back to ASCII."""
    import builtins
    from adb_wifi_connect.connect import _render_sixel_qr
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == 'sixel':
            raise ImportError("No module named 'sixel'")
        return real_import(name, *args, **kwargs)

    with patch.object(builtins, '__import__', side_effect=fake_import):
        with pytest.raises(ImportError):
            _render_sixel_qr("data")


def test_render_sixel_qr_outputs_dcs_string():
    pytest.importorskip("sixel")
    pytest.importorskip("PIL")
    from adb_wifi_connect.connect import _render_sixel_qr
    out = _render_sixel_qr("WIFI:T:ADB;S:a;P:b;;")
    assert out.startswith("\x1bP")
    assert out.endswith("\x1b\\")


def test_render_sixel_qr_draws_light_background():
    """QR must be visible on dark terminals: the render must include the
    light background pixels (not chromakey-transparent) so black modules
    have something to contrast against."""
    pytest.importorskip("sixel")
    pytest.importorskip("PIL")
    import re
    from adb_wifi_connect.connect import _render_sixel_qr
    out = _render_sixel_qr("WIFI:T:ADB;S:a;P:b;;")
    header = re.match(r"\x1bP([0-9;]+)q", out)
    assert header, "malformed DCS header"
    dcs_args = header.group(1).split(";")
    assert dcs_args[1] != "2", "background-select=2 makes QR background transparent"
    palette = sorted(set(re.findall(r"#[0-9]+;2;([0-9;]+)", out)))
    assert any(all(int(v) > 80 for v in p.split(";")) for p in palette), \
        f"no light color in palette: {palette}"


def test_connect_single_device_passes_qr_mode_to_display_qr():
    with patch('adb_wifi_connect.flow.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.flow.sys.stdin.isatty', return_value=True), \
         patch('adb_wifi_connect.flow.display_qr') as mock_qr, \
         patch('adb_wifi_connect.flow.wait_for_scanned_event',
               return_value="manual"), \
         patch('adb_wifi_connect.flow.ask_pairing_code', return_value="123456"), \
         patch('adb_wifi_connect.flow.ask_pairing_port', return_value=37000), \
         patch('adb_wifi_connect.flow.run_adb_pair', return_value=True), \
         patch('adb_wifi_connect.flow.discover_connection_port', return_value=(None, None)), \
         patch('adb_wifi_connect.flow.scan_for_adb_port', return_value=5555):
        from adb_wifi_connect.connect import connect_single_device
        connect_single_device("1.2.3.4", manual=False, skip_pair=False,
                              qr_mode="sixel")
    mock_qr.assert_called_once()
    assert mock_qr.call_args.kwargs.get('mode') == 'sixel'


# --- --on-connect hook ---

def test_run_on_connect_executes_command_with_target_env():
    from adb_wifi_connect.connect import run_on_connect
    with patch('subprocess.run') as m:
        run_on_connect("scrcpy", "1.2.3.4:5555")
    m.assert_called_once()
    assert m.call_args[0][0] == "scrcpy"
    assert m.call_args[1].get('shell') is True
    env = m.call_args[1].get('env')
    assert env is not None
    assert env.get('AWC_TARGET') == "1.2.3.4:5555"


def test_run_on_connect_swallows_failures():
    from adb_wifi_connect.connect import run_on_connect
    with patch('subprocess.run', side_effect=OSError("nope")), \
         patch('builtins.print'):
        run_on_connect("bad-cmd", "1.2.3.4:5555")  # must not raise


def test_run_on_connect_noop_without_command():
    from adb_wifi_connect.connect import run_on_connect
    with patch('subprocess.run') as m:
        run_on_connect(None, "1.2.3.4:5555")
        run_on_connect("", "1.2.3.4:5555")
    m.assert_not_called()


def test_connect_single_device_runs_hook_on_success():
    with patch('adb_wifi_connect.flow.is_device_connected', return_value=5555), \
         patch('adb_wifi_connect.flow.run_on_connect') as hook:
        from adb_wifi_connect.connect import connect_single_device
        result = connect_single_device("1.2.3.4", skip_pair=True, manual=True,
                                       on_connect="scrcpy")
    assert result == "1.2.3.4:5555"
    hook.assert_called_once_with("scrcpy", "1.2.3.4:5555")


def test_connect_single_device_runs_hook_on_new_connection():
    with patch('adb_wifi_connect.flow.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.flow.discover_connection_port', return_value=(None, None)), \
         patch('adb_wifi_connect.flow.scan_for_adb_port', return_value=37689), \
         patch('adb_wifi_connect.flow.run_on_connect') as hook:
        from adb_wifi_connect.connect import connect_single_device
        result = connect_single_device("1.2.3.4", skip_pair=True, manual=True,
                                       on_connect="scrcpy")
    assert result == "1.2.3.4:37689"
    hook.assert_called_once_with("scrcpy", "1.2.3.4:37689")


def test_connect_single_device_does_not_run_hook_on_failure():
    with patch('adb_wifi_connect.flow.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.flow.discover_connection_port', return_value=(None, None)), \
         patch('adb_wifi_connect.flow.scan_for_adb_port', return_value=None), \
         patch('adb_wifi_connect.flow.run_adb_connect', return_value=False), \
         patch('adb_wifi_connect.flow.run_on_connect') as hook:
        from adb_wifi_connect.connect import connect_single_device
        result = connect_single_device("1.2.3.4", skip_pair=True, manual=True,
                                       on_connect="scrcpy")
    assert result is None
    hook.assert_not_called()


def test_main_on_connect_flag_threads_through():
    devices = [("192.168.1.10", "Pixel", False)]
    with patch('adb_wifi_connect.cli.discover_all_phones', return_value=devices), \
         patch('adb_wifi_connect.cli.connect_single_device',
               return_value="192.168.1.10:5555") as csd, \
         patch('sys.argv', ['awc', '--on-connect', 'scrcpy']):
        from adb_wifi_connect.connect import main
        main()
    assert csd.call_args.kwargs.get('on_connect') == 'scrcpy'


def test_main_qr_defaults_to_ascii():
    """DA1 cannot verify that Sixel actually renders (Konsole advertises
    attribute 4 but paints nothing), so the CLI default must be ascii."""
    devices = [("192.168.1.10", "Pixel", False)]
    with patch('adb_wifi_connect.cli.discover_all_phones', return_value=devices), \
         patch('adb_wifi_connect.cli.connect_single_device',
               return_value="192.168.1.10:5555") as csd, \
         patch('sys.argv', ['awc']):
        from adb_wifi_connect.connect import main
        main()
    assert csd.call_args.kwargs.get('qr_mode') == 'ascii'


# --- --watch daemon mode ---

def test_watch_listener_connect_service_marks_paired():
    from adb_wifi_connect.connect import WatchListener, MDNS_CONNECT_TYPE
    state = {}
    listener = WatchListener(state)
    zc = MagicMock()
    info = MagicMock()
    info.server = "1.2.3.4"
    info.port = 37000
    zc.get_service_info.return_value = info
    listener.add_service(zc, MDNS_CONNECT_TYPE, "svc-a")
    assert state == {"1.2.3.4": True}


def test_watch_listener_pair_service_marks_pair():
    from adb_wifi_connect.connect import WatchListener, MDNS_TYPE
    state = {}
    listener = WatchListener(state)
    zc = MagicMock()
    info = MagicMock()
    info.server = "1.2.3.4"
    info.port = 37000
    zc.get_service_info.return_value = info
    listener.add_service(zc, MDNS_TYPE, "svc-b")
    assert state == {"1.2.3.4": "pair"}


def test_watch_listener_connect_overwrites_pair():
    from adb_wifi_connect.connect import WatchListener, MDNS_TYPE, MDNS_CONNECT_TYPE
    state = {}
    listener = WatchListener(state)
    zc = MagicMock()
    info = MagicMock()
    info.server = "1.2.3.4"
    info.port = 37000
    zc.get_service_info.return_value = info
    listener.add_service(zc, MDNS_TYPE, "svc-pair")
    listener.add_service(zc, MDNS_CONNECT_TYPE, "svc-connect")
    assert state["1.2.3.4"] is True


def test_watch_listener_pair_does_not_downgrade_connect():
    from adb_wifi_connect.connect import WatchListener, MDNS_TYPE, MDNS_CONNECT_TYPE
    state = {}
    listener = WatchListener(state)
    zc = MagicMock()
    info = MagicMock()
    info.server = "1.2.3.4"
    info.port = 37000
    zc.get_service_info.return_value = info
    listener.add_service(zc, MDNS_CONNECT_TYPE, "svc-connect")
    listener.add_service(zc, MDNS_TYPE, "svc-pair")
    assert state["1.2.3.4"] is True


def test_watch_listener_remove_marks_disconnected():
    from adb_wifi_connect.connect import WatchListener, MDNS_CONNECT_TYPE
    state = {}
    listener = WatchListener(state)
    zc = MagicMock()
    info = MagicMock()
    info.server = "1.2.3.4"
    info.port = 37000
    zc.get_service_info.return_value = info
    listener.add_service(zc, MDNS_CONNECT_TYPE, "svc-connect")
    listener.remove_service(zc, MDNS_CONNECT_TYPE, "svc-connect")
    assert state["1.2.3.4"] is False


def test_watch_cycle_connects_down_device():
    from adb_wifi_connect.connect import watch_cycle
    state, cache = {}, set()
    with patch('adb_wifi_connect.watch.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.watch.connect_single_device',
               return_value="1.2.3.4:5555") as csd:
        results = watch_cycle(["1.2.3.4"], state, cache, skip_pair=True,
                              on_connect="scrcpy")
    csd.assert_called_once_with("1.2.3.4", skip_pair=True, manual=False,
                                qr_mode="auto", on_connect="scrcpy")
    assert results["1.2.3.4"] == "1.2.3.4:5555"
    assert "1.2.3.4" in cache


def test_watch_cycle_skips_connected_device():
    from adb_wifi_connect.connect import watch_cycle
    state, cache = {}, set()
    with patch('adb_wifi_connect.watch.is_device_connected', return_value=5555), \
         patch('adb_wifi_connect.watch.connect_single_device') as csd:
        results = watch_cycle(["1.2.3.4"], state, cache, skip_pair=True)
    csd.assert_not_called()
    assert results["1.2.3.4"] == "1.2.3.4:5555"
    assert "1.2.3.4" in cache


def test_watch_cycle_skips_removed_device():
    from adb_wifi_connect.connect import watch_cycle
    state, cache = {"1.2.3.4": False}, set()
    with patch('adb_wifi_connect.watch.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.watch.connect_single_device') as csd:
        results = watch_cycle(["1.2.3.4"], state, cache, skip_pair=True)
    csd.assert_not_called()
    assert "1.2.3.4" not in results


def test_watch_cycle_connect_service_forces_skip_pair():
    from adb_wifi_connect.connect import watch_cycle
    state, cache = {"1.2.3.4": True}, set()
    with patch('adb_wifi_connect.watch.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.watch.connect_single_device',
               return_value="1.2.3.4:5555") as csd:
        watch_cycle(["1.2.3.4"], state, cache, skip_pair=False)
    assert csd.call_args.kwargs.get('skip_pair') is True


def test_watch_cycle_pair_service_keeps_user_skip_pair():
    from adb_wifi_connect.connect import watch_cycle
    state, cache = {"1.2.3.4": "pair"}, set()
    with patch('adb_wifi_connect.watch.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.watch.connect_single_device',
               return_value="1.2.3.4:5555") as csd:
        watch_cycle(["1.2.3.4"], state, cache, skip_pair=False)
    assert csd.call_args.kwargs.get('skip_pair') is False


def test_watch_cycle_downgrades_state_after_failed_silent_connect():
    from adb_wifi_connect.connect import watch_cycle
    state, cache = {"1.2.3.4": True}, set()
    with patch('adb_wifi_connect.watch.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.watch.connect_single_device',
               return_value=None) as csd:
        watch_cycle(["1.2.3.4"], state, cache, skip_pair=False)
    # silent (already-paired) attempt failed -> next cycle offers pairing
    assert state["1.2.3.4"] == "pair"
    with patch('adb_wifi_connect.watch.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.watch.connect_single_device',
               return_value=None) as csd:
        watch_cycle(["1.2.3.4"], state, cache, skip_pair=False)
    assert csd.call_args.kwargs.get('skip_pair') is False


def test_watch_cycle_does_not_downgrade_when_user_forced_skip_pair():
    from adb_wifi_connect.connect import watch_cycle
    state, cache = {"1.2.3.4": True}, set()
    with patch('adb_wifi_connect.watch.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.watch.connect_single_device', return_value=None):
        watch_cycle(["1.2.3.4"], state, cache, skip_pair=True)
    assert state["1.2.3.4"] is True


def test_watch_cycle_reconnects_after_drop():
    from adb_wifi_connect.connect import watch_cycle
    state, cache = {}, set()
    with patch('adb_wifi_connect.watch.is_device_connected',
               side_effect=[5555, None, 5555]), \
         patch('adb_wifi_connect.watch.connect_single_device',
               return_value="1.2.3.4:37000") as csd:
        r1 = watch_cycle(["1.2.3.4"], state, cache, skip_pair=True)
        assert r1["1.2.3.4"] == "1.2.3.4:5555"
        csd.assert_not_called()
        r2 = watch_cycle(["1.2.3.4"], state, cache, skip_pair=True)
        assert r2["1.2.3.4"] == "1.2.3.4:37000"
        csd.assert_called_once()


def test_watch_loop_runs_cycles_then_exits():
    from adb_wifi_connect.connect import watch_loop
    with patch('adb_wifi_connect.watch._start_watch_browsers', return_value=(None, [])), \
         patch('adb_wifi_connect.watch.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.watch.connect_single_device',
               return_value="1.2.3.4:5555") as csd:
        results = watch_loop(["1.2.3.4"], poll_interval=0, max_cycles=2,
                             skip_pair=True, rescan=False)
    assert csd.call_count == 2
    assert results["1.2.3.4"] == "1.2.3.4:5555"


def test_watch_loop_keyboard_interrupt_exits_and_cleans_up():
    from adb_wifi_connect.connect import watch_loop
    zc, browser = MagicMock(), MagicMock()
    with patch('adb_wifi_connect.watch._start_watch_browsers', return_value=(zc, [browser])), \
         patch('adb_wifi_connect.watch.watch_cycle', side_effect=KeyboardInterrupt):
        results = watch_loop(["1.2.3.4"], poll_interval=0)
    assert results == {}
    browser.cancel.assert_called_once()
    zc.close.assert_called_once()


def test_watch_loop_survives_browser_start_failure():
    from adb_wifi_connect.connect import watch_loop
    with patch('adb_wifi_connect.watch._start_watch_browsers',
               side_effect=RuntimeError("boom")), \
         patch('adb_wifi_connect.watch.is_device_connected', return_value=5555):
        results = watch_loop(["1.2.3.4"], poll_interval=0, max_cycles=1)
    assert results["1.2.3.4"] == "1.2.3.4:5555"


def test_watch_rescan_remarks_returned_device():
    from adb_wifi_connect.connect import _watch_rescan
    state = {"1.2.3.4": False, "9.9.9.9": False}
    with patch('adb_wifi_connect.watch.discover_all_phones',
               return_value=[("1.2.3.4", "Pixel", False)]):
        _watch_rescan(["1.2.3.4", "9.9.9.9"], state)
    assert state["1.2.3.4"] is True
    assert state["9.9.9.9"] is False  # not discovered -> unchanged


def test_watch_rescan_tolerates_extended_records():
    """_watch_rescan accepts extended 6-tuple discovery records — the old
    strict 3-tuple unpack raised ValueError on them."""
    from adb_wifi_connect.watch import _watch_rescan
    state = {"1.2.3.4": False, "9.9.9.9": False}
    discovered = [
        ("1.2.3.4", "Pixel", False, True, 41123, None),
        ("8.8.8.8", "Other", False, False, None, None),
    ]
    with patch('adb_wifi_connect.watch.discover_all_phones',
               return_value=discovered):
        _watch_rescan(["1.2.3.4", "9.9.9.9"], state)
    assert state["1.2.3.4"] is True
    assert state["9.9.9.9"] is False  # not discovered -> unchanged
    assert "8.8.8.8" not in state  # non-target record ignored


def test_start_watch_browsers_without_zeroconf():
    from adb_wifi_connect.connect import _start_watch_browsers
    with patch('adb_wifi_connect.watch.Zeroconf', None):
        zc, browsers = _start_watch_browsers(MagicMock())
    assert zc is None
    assert browsers == []


def test_main_watch_with_ip_targets_that_device():
    with patch('adb_wifi_connect.cli.watch_loop') as wl, \
         patch('adb_wifi_connect.cli.discover_all_phones') as disc, \
         patch('sys.argv', ['awc', '--watch', '--ip', '1.2.3.4', '--skip-pair']):
        from adb_wifi_connect.connect import main
        main()
    disc.assert_not_called()
    wl.assert_called_once()
    assert wl.call_args[0][0] == ["1.2.3.4"]
    assert wl.call_args.kwargs.get('skip_pair') is True


def test_main_watch_single_discovered_device():
    devices = [("192.168.1.10", "Pixel", False)]
    with patch('adb_wifi_connect.cli.discover_all_phones', return_value=devices), \
         patch('adb_wifi_connect.cli.watch_loop') as wl, \
         patch('adb_wifi_connect.cli.connect_single_device') as csd, \
         patch('sys.argv', ['awc', '--watch', '--on-connect', 'scrcpy']):
        from adb_wifi_connect.connect import main
        main()
    csd.assert_not_called()
    wl.assert_called_once()
    assert wl.call_args[0][0] == ["192.168.1.10"]
    assert wl.call_args.kwargs.get('on_connect') == 'scrcpy'


def test_main_watch_multiple_uses_selection():
    devices = [
        ("192.168.1.10", "Pixel", False),
        ("192.168.1.11", "Samsung", False),
    ]
    with patch('adb_wifi_connect.cli.discover_all_phones', return_value=devices), \
         patch('adb_wifi_connect.cli.select_devices', return_value=[1]), \
         patch('adb_wifi_connect.cli.watch_loop') as wl, \
         patch('sys.argv', ['awc', '--watch']):
        from adb_wifi_connect.connect import main
        main()
    wl.assert_called_once()
    assert wl.call_args[0][0] == ["192.168.1.11"]


# --- packaging config (awc entry point, backend) ---

def test_pyproject_scripts_and_backend():
    tomllib = pytest.importorskip("tomllib")
    path = os.path.join(os.path.dirname(__file__), '..', 'pyproject.toml')
    with open(path, 'rb') as f:
        data = tomllib.load(f)
    assert data['build-system']['build-backend'] == 'setuptools.build_meta'
    scripts = data['project']['scripts']
    assert scripts.get('awc') == 'adb_wifi_connect.cli:run'
    assert scripts.get('adb-connect') == 'adb_wifi_connect.cli:run'
    extras = data['project']['optional-dependencies']
    assert 'sixel' in extras['all']


def test_main_adb_path_flag_sets_adb_path():
    devices = [("192.168.1.10", "Pixel", False)]
    old_path = adb.ADB_PATH
    try:
        with patch('adb_wifi_connect.cli.discover_all_phones', return_value=devices), \
             patch('adb_wifi_connect.cli.connect_single_device',
                   return_value="192.168.1.10:5555"), \
             patch('sys.argv',
                   ['awc', '--adb-path', '/opt/platform-tools/adb']):
            from adb_wifi_connect.connect import main
            main()
        assert adb.ADB_PATH == "/opt/platform-tools/adb"
    finally:
        adb.ADB_PATH = old_path


# --- discovery early-exit (no fixed full-timeout sleeps) ---

def test_discover_all_phones_returns_early_when_found():
    """Once a device is visible, return after the grace window —
    not after the full timeout (lyto-style reactivity)."""
    import time as _time
    mock_proc = MagicMock()
    mock_proc.stdout = "List of devices attached\n\n"
    with patch('adb_wifi_connect.mdns.Zeroconf') as mock_zc_cls, \
         patch('adb_wifi_connect.mdns.ServiceBrowser') as mock_browser_cls, \
         patch('adb_wifi_connect.adb.subprocess.run', return_value=mock_proc):
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc
        info = MagicMock()
        info.server = "192.168.1.167"
        info.port = 37000

        def fake_browser(zc, svc_type, listener):
            listener.add_service(zc, svc_type, "svc")
            return MagicMock()

        mock_browser_cls.side_effect = fake_browser
        with patch.object(mock_zc, 'get_service_info', return_value=info):
            with patch.object(mock_zc, 'close'):
                from adb_wifi_connect.connect import discover_all_phones
                start = _time.monotonic()
                result = discover_all_phones(timeout=6, grace=0.05)
                elapsed = _time.monotonic() - start
    assert len(result) == 1
    assert elapsed < 3, f"took {elapsed:.1f}s — should not wait full timeout"


def test_discover_all_phones_grace_window_collects_stragglers():
    """A second device appearing within the grace window is still found."""
    import time as _time
    mock_proc = MagicMock()
    mock_proc.stdout = "List of devices attached\n\n"
    with patch('adb_wifi_connect.mdns.Zeroconf') as mock_zc_cls, \
         patch('adb_wifi_connect.mdns.ServiceBrowser') as mock_browser_cls, \
         patch('adb_wifi_connect.adb.subprocess.run', return_value=mock_proc):
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc
        fired = []

        def make_info(ip):
            info = MagicMock()
            info.server = ip
            info.port = 37000
            return info

        def fake_browser(zc, svc_type, listener):
            # first device immediately, second after 0.4s from another thread
            listener.add_service(zc, svc_type, "svc-1")

            def late():
                _time.sleep(0.4)
                listener.add_service(zc, svc_type, "svc-2")

            threading.Thread(target=late, daemon=True).start()
            return MagicMock()

        # get_service_info must return the right IP per call
        infos = {"svc-1": make_info("192.168.1.167"),
                 "svc-2": make_info("192.168.1.172")}

        def get_info(svc_type, name):
            return infos[name]

        mock_browser_cls.side_effect = fake_browser
        with patch.object(mock_zc, 'get_service_info', side_effect=get_info):
            with patch.object(mock_zc, 'close'):
                from adb_wifi_connect.connect import discover_all_phones
                result = discover_all_phones(timeout=6, grace=1.0)
    ips = {r[0] for r in result}
    assert "192.168.1.167" in ips
    assert "192.168.1.172" in ips


def test_discover_all_phones_waits_full_timeout_when_nothing_found():
    import time as _time
    with patch('adb_wifi_connect.mdns.Zeroconf') as mock_zc_cls, \
         patch('adb_wifi_connect.mdns.ServiceBrowser') as mock_browser_cls:
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc
        mock_browser_cls.return_value = MagicMock()
        with patch.object(mock_zc, 'close'):
            from adb_wifi_connect.connect import discover_all_phones
            start = _time.monotonic()
            result = discover_all_phones(timeout=1, grace=0.5)
            elapsed = _time.monotonic() - start
    assert result == []
    assert elapsed >= 1  # can't exit early if nothing was ever seen


def test_discover_all_phones_grace_requires_server_qualified_info():
    """Grace starts only on infos with a truthy .server — the original
    found_devices guard. A server-less info is not a device sighting and
    must not trigger the grace-window early exit."""
    import time as _time
    mock_proc = MagicMock()
    mock_proc.stdout = "List of devices attached\n\n"

    def run_case(info):
        with patch('adb_wifi_connect.mdns.Zeroconf') as mock_zc_cls, \
             patch('adb_wifi_connect.mdns.ServiceBrowser') as mock_browser_cls, \
             patch('adb_wifi_connect.adb.subprocess.run', return_value=mock_proc):
            mock_zc = MagicMock()
            mock_zc_cls.return_value = mock_zc

            def fake_browser(zc, svc_type, listener):
                listener.add_service(zc, svc_type, "svc")
                return MagicMock()

            mock_browser_cls.side_effect = fake_browser
            with patch.object(mock_zc, 'get_service_info', return_value=info):
                with patch.object(mock_zc, 'close'):
                    from adb_wifi_connect.connect import discover_all_phones
                    start = _time.monotonic()
                    result = discover_all_phones(timeout=3, grace=0.1)
                    elapsed = _time.monotonic() - start
        return result, elapsed

    # Server-less info: grace must not start — browse runs full timeout
    serverless = MagicMock()
    serverless.server = None
    serverless.port = 37000
    result, elapsed = run_case(serverless)
    assert result == []
    assert elapsed >= 2.9, \
        f"took {elapsed:.1f}s — server-less info must not start grace"

    # Truthy server: sighting recorded — grace window honored (early exit)
    info = MagicMock()
    info.server = "192.168.1.167"
    info.port = 37000
    result, elapsed = run_case(info)
    assert len(result) == 1
    assert result[0][0] == "192.168.1.167"
    assert elapsed < 1.5, f"took {elapsed:.1f}s — grace should early-exit"


# --- watch instant wake on mDNS events (no fixed 30s sleep) ---

def test_watch_listener_add_sets_wake_event():
    from adb_wifi_connect.connect import WatchListener, MDNS_CONNECT_TYPE
    wake = threading.Event()
    listener = WatchListener({}, wake=wake)
    zc = MagicMock()
    info = MagicMock()
    info.server = "1.2.3.4"
    info.port = 37000
    zc.get_service_info.return_value = info
    listener.add_service(zc, MDNS_CONNECT_TYPE, "svc")
    assert wake.is_set()


def test_watch_listener_remove_does_not_set_wake_event():
    from adb_wifi_connect.connect import WatchListener, MDNS_CONNECT_TYPE
    wake = threading.Event()
    listener = WatchListener({}, wake=wake)
    listener.remove_service(MagicMock(), MDNS_CONNECT_TYPE, "svc")
    assert not wake.is_set()


def test_watch_loop_wakes_immediately_on_listener_event():
    """A service reappearing must trigger the next cycle right away,
    not after the full poll_interval."""
    import time as _time
    from adb_wifi_connect.connect import watch_loop, MDNS_CONNECT_TYPE
    captured = {}

    def fake_start(listener):
        captured['listener'] = listener
        return (None, [])

    with patch('adb_wifi_connect.watch._start_watch_browsers', side_effect=fake_start), \
         patch('adb_wifi_connect.watch.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.watch.connect_single_device',
               return_value="1.2.3.4:5555") as csd:
        outcome = {}

        def run():
            outcome['results'] = watch_loop(
                ["1.2.3.4"], poll_interval=30, max_cycles=2, rescan=False)

        t = threading.Thread(target=run, daemon=True)
        t.start()
        # wait for the first cycle to run
        for _ in range(100):
            if csd.call_count >= 1:
                break
            _time.sleep(0.05)
        assert csd.call_count >= 1, "first watch cycle never ran"

        # phone reappears -> listener event must wake the loop now
        zc = MagicMock()
        info = MagicMock()
        info.server = "1.2.3.4"
        info.port = 37000
        zc.get_service_info.return_value = info
        start = _time.monotonic()
        captured['listener'].add_service(zc, MDNS_CONNECT_TYPE, "svc-x")
        t.join(timeout=5)
        elapsed = _time.monotonic() - start

    assert not t.is_alive(), "loop did not wake — still sleeping poll_interval"
    assert elapsed < 5, f"wake took {elapsed:.1f}s"
    assert csd.call_count == 2
    assert outcome['results']["1.2.3.4"] == "1.2.3.4:5555"


# --- KeyboardInterrupt guards (D5) ---

def test_main_ki_guard_prints_cancelled_exits_1(capsys):
    # main --ip path: _guarded wraps `connect(...)` looked up in cli
    with patch('adb_wifi_connect.cli.connect',
               side_effect=KeyboardInterrupt), \
         patch('sys.argv', ['awc', '--ip', '1.2.3.4']):
        from adb_wifi_connect.cli import main
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 1
    assert "Cancelled." in capsys.readouterr().out


def test_connect_ki_guard_prints_cancelled_exits_1(capsys):
    # flow.connect has its own guard around _connect_inner
    with patch('adb_wifi_connect.flow.connect_single_device',
               side_effect=KeyboardInterrupt):
        from adb_wifi_connect.flow import connect
        with pytest.raises(SystemExit) as exc:
            connect(ip="1.2.3.4")
    assert exc.value.code == 1
    assert "Cancelled." in capsys.readouterr().out
