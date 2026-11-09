import queue
import sys
from unittest.mock import patch, MagicMock

import pytest


# --- console wrapper: no repr dump, exit 0 on success ---

def test_run_wrapper_discards_main_return_value():
    from adb_wifi_connect.cli import run
    with patch('adb_wifi_connect.cli.main',
               return_value=["1.2.3.4:5555", "1.2.3.5:5555"]):
        assert run() is None  # sys.exit(None) -> exit 0, nothing printed


def test_run_wrapper_propagates_exit_codes():
    from adb_wifi_connect.cli import run
    with patch('adb_wifi_connect.cli.main', side_effect=SystemExit(1)):
        with pytest.raises(SystemExit) as exc:
            run()
    assert exc.value.code == 1


def test_help_documents_in_selector_qr_is_ascii(capsys):
    """Spec §2: the in-selector QR is always ASCII (a Sixel image cannot
    be redrawn inside curses) — documented in `awc --help`'s `--qr`
    text, not only in docs/configuration."""
    with patch('sys.argv', ['awc', '--help']):
        from adb_wifi_connect.cli import main
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--qr" in out, out
    assert "in-selector" in out, out   # the ASCII-only clause
    assert "curses" in out, out


# --- Ctrl+C during selection: clean exit, never a traceback ---

def test_run_net_catches_keyboard_interrupt_from_main(capsys):
    """KI escaping main() (bare interactive site) -> run()'s net:
    Cancelled. + exit 1, never a traceback."""
    from adb_wifi_connect.cli import run
    with patch('adb_wifi_connect.cli.main',
               side_effect=KeyboardInterrupt):
        with pytest.raises(SystemExit) as exc:
            run()
    assert exc.value.code == 1
    assert "Cancelled." in capsys.readouterr().out


def test_run_keyboard_interrupt_in_selection_exits_cleanly(capsys):
    """KI from the selector -> Cancelled. + exit 1, never a traceback."""
    from adb_wifi_connect import cli
    with patch('adb_wifi_connect.cli.load_env'), \
         patch('adb_wifi_connect.cli.start_pairing_session'), \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('adb_wifi_connect.cli.enrich_devices', create=True), \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[('1.2.3.4', 'devA', False, False, None, None),
                             ('1.2.3.5', 'devB', False, False, None, None)]), \
         patch('adb_wifi_connect.cli.select_devices',
               side_effect=KeyboardInterrupt), \
         patch('sys.argv', ['awc']):
        with pytest.raises(SystemExit) as exc:
            cli.run()
    assert exc.value.code == 1
    assert "Cancelled." in capsys.readouterr().out


def test_main_keyboard_interrupt_in_selection_phase_guard(capsys):
    """The phase guard itself: main() wraps select_devices in
    _guarded, so KI there -> SystemExit(1) + Cancelled., not a raw
    KeyboardInterrupt escaping main()."""
    from adb_wifi_connect import cli
    with patch('adb_wifi_connect.cli.load_env'), \
         patch('adb_wifi_connect.cli.start_pairing_session'), \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('adb_wifi_connect.cli.enrich_devices', create=True), \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[('1.2.3.4', 'devA', False, False, None, None),
                             ('1.2.3.5', 'devB', False, False, None, None)]), \
         patch('adb_wifi_connect.cli.select_devices',
               side_effect=KeyboardInterrupt), \
         patch('sys.argv', ['awc']):
        with pytest.raises(SystemExit) as exc:
            cli.main()
    assert exc.value.code == 1
    assert "Cancelled." in capsys.readouterr().out


# --- single-device output: result printed exactly once ---

def test_single_device_output_contains_result_once(capsys):
    with patch('adb_wifi_connect.cli.connect',
               return_value="1.2.3.4:5555"), \
         patch('sys.argv', ['awc', '--ip', '1.2.3.4']):
        from adb_wifi_connect.cli import main
        main()
    out = capsys.readouterr().out
    assert out.count("1.2.3.4:5555") == 1, f"expected 1 occurrence, got:\n{out}"


# --- flow announces the final state exactly once ---

def test_flow_fresh_connect_prints_connected_line(capsys):
    from adb_wifi_connect.flow import connect_single_device
    with patch('adb_wifi_connect.flow.is_device_connected',
               return_value=None), \
         patch('adb_wifi_connect.flow.discover_connection_port',
               return_value=(None, None)), \
         patch('adb_wifi_connect.flow.scan_for_adb_port',
               return_value=37689):
        result = connect_single_device("1.2.3.4", skip_pair=True,
                                       manual=True)
    assert result == "1.2.3.4:37689"
    out = capsys.readouterr().out
    assert "Connected: 1.2.3.4:37689" in out, f"missing final line:\n{out}"


def test_flow_already_connected_not_double_announced(capsys):
    from adb_wifi_connect.flow import connect_single_device
    with patch('adb_wifi_connect.flow.is_device_connected',
               return_value=5555):
        result = connect_single_device("1.2.3.4", skip_pair=True,
                                       manual=True)
    assert result == "1.2.3.4:5555"
    out = capsys.readouterr().out
    assert out.count("Already connected: 1.2.3.4:5555") == 1
    # no second "Connected:" announcement on top of it
    assert "  Connected: 1.2.3.4:5555" not in out


# --- multi-device CLI: headers + no per-device duplicates ---

def test_multi_cli_headers_cleaned_and_result_once(capsys):
    # names arrive pre-cleaned from mdns (see
    # test_discover_all_phones_returns_clean_names)
    devices = [
        ("1.2.3.4", "X123-y0", True),
        ("1.2.3.5", "X999-z9", False),
    ]
    with patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=devices), \
         patch('adb_wifi_connect.cli.connect_single_device',
               side_effect=lambda ip, **kw: f"{ip}:5555"), \
         patch('sys.argv', ['awc', '--all']):
        from adb_wifi_connect.cli import main
        main()
    out = capsys.readouterr().out
    assert "--- X123-y0 (1.2.3.4) ---" in out, out
    assert "_adb-tls" not in out, "raw mDNS instance name leaked into UI"
    # result appears once — in the summary only, not also per-device
    assert out.count("1.2.3.5:5555") == 1, out


def test_batch_drain_appends_scanned_phone(capsys):
    """A scan that lands while the selector is open joins the batch: its
    record comes straight from the event payload, is force-checked into
    the selection, and the summary counts it (already-connected
    short-circuit is flow's job — one status line each)."""
    events = queue.Queue()
    session = MagicMock()
    session.events = events
    session.result = None
    payload = {"kind": "scanned", "ip": "10.0.0.3", "short": "PIXEL3",
               "port": 42000, "target": "10.0.0.3:42000",
               "identity": {"brand": "google", "model": "Pixel 9"}}
    devices = [
        ("1.2.3.4", "X1", True, True, 40000, None),
        ("1.2.3.5", "X2", False, True, 40001, None),
        ("1.2.3.6", "X3", False, False, None, None),
    ]

    def select_then_scan(devices, is_tty=False, **kwargs):
        # **kwargs: cli now threads qr_text/session into the selector
        events.put(payload)  # scanned while the selector runs
        return [0]

    with patch('adb_wifi_connect.cli.start_pairing_session',
               return_value=session), \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=devices), \
         patch('adb_wifi_connect.cli.enrich_devices', side_effect=list), \
         patch('adb_wifi_connect.cli.select_devices',
               side_effect=select_then_scan), \
         patch('adb_wifi_connect.cli.connect_single_device',
               side_effect=lambda ip, **kw: f"{ip}:41234") as mock_connect, \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('sys.stdin.isatty', return_value=True), \
         patch('sys.argv', ['awc']):
        from adb_wifi_connect.cli import main
        result = main()
    connected = [c.args[0] for c in mock_connect.call_args_list]
    assert connected == ["1.2.3.4", "10.0.0.3"], connected
    appended = mock_connect.call_args_list[1].kwargs
    assert appended.get("paired") is True          # payload -> rec[3]
    assert appended.get("short_name") == "PIXEL3"  # payload -> rec[1]
    assert appended.get("connect_port") == 42000   # payload -> rec[4]
    assert appended.get("session") is session
    out = capsys.readouterr().out
    assert "Connected 2/2 devices" in out, out
    assert result == ["1.2.3.4:41234", "10.0.0.3:41234"]


def test_discover_all_phones_returns_clean_names():
    """Raw mDNS instance names are cleaned at the source, so every
    consumer (headers, --list, curses UI) shows short names."""
    mock_proc = MagicMock()
    mock_proc.stdout = "List of devices attached\n\n"
    with patch('adb_wifi_connect.mdns.Zeroconf') as mock_zc_cls, \
         patch('adb_wifi_connect.mdns.ServiceBrowser') as mock_browser_cls, \
         patch('adb_wifi_connect.adb.subprocess.run',
               return_value=mock_proc):
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc
        info = MagicMock()
        info.server = "1.2.3.4"
        info.port = 37000
        info.name = "adb-X123-y0._adb-tls-connect._tcp.local."

        def fake_browser(zc, svc_type, listener):
            # _browse drives on_info through the listener's add_service
            listener.add_service(zc, svc_type, "svc")
            return MagicMock()

        mock_browser_cls.side_effect = fake_browser
        with patch.object(mock_zc, 'get_service_info', return_value=info):
            from adb_wifi_connect.mdns import discover_all_phones
            result = discover_all_phones(timeout=5, grace=0.05)

    assert result[0][1] == "X123-y0"


def test_short_service_name():
    from adb_wifi_connect.mdns import _short_service_name
    assert _short_service_name(
        "adb-A1B2C3D4E5F6-xyz123._adb-tls-connect._tcp.local."
    ) == "A1B2C3D4E5F6-xyz123"
    assert _short_service_name(
        "pixel-pair-1234._adb-tls-pairing._tcp.local."
    ) == "pixel-pair-1234"
    assert _short_service_name("Pixel_7") == "Pixel_7"


# --- desired-state selection: unchecked connected devices disconnect ---

def test_run_adb_disconnect_uses_argv():
    """disconnect targets the exact live serial from the devices snapshot
    (a bare ip makes adb misparse ip:5555) — argv list, no shell=True."""
    from adb_wifi_connect.adb import run_adb_disconnect
    with patch('adb_wifi_connect.adb.subprocess.run') as mock_run:
        mock_run.side_effect = [
            MagicMock(
                stdout="List of devices attached\n1.2.3.4:41234\tdevice\n\n",
                returncode=0),
            MagicMock(stdout="disconnected 1.2.3.4:41234", returncode=0),
        ]
        removed = run_adb_disconnect("1.2.3.4")
    disc_calls = [c for c in mock_run.call_args_list
                  if c[0][0][1] == "disconnect"]
    assert len(disc_calls) == 1
    assert disc_calls[0][0][0] == ["adb", "disconnect", "1.2.3.4:41234"]
    assert "shell" not in disc_calls[0][1]
    assert removed == ["1.2.3.4:41234"]


def test_multi_cli_disconnects_unselected_connected(capsys):
    devices = [
        ("1.2.3.4", "adb-X123-y0._adb-tls-connect._tcp.local.", True),
        ("1.2.3.5", "adb-X999-z9._adb-tls-connect._tcp.local.", False),
    ]
    with patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=devices), \
         patch('adb_wifi_connect.cli.select_devices', return_value=[1]), \
         patch('adb_wifi_connect.cli.connect_single_device',
               side_effect=lambda ip, **kw: f"{ip}:5555"), \
         patch('adb_wifi_connect.adb.run_adb_disconnect',
               return_value=["1.2.3.4:41234"]) as mock_disc, \
         patch('sys.argv', ['awc']):
        from adb_wifi_connect.cli import main
        main()
    mock_disc.assert_called_once_with(
        "1.2.3.4", "adb-X123-y0._adb-tls-connect._tcp.local.")
    # truthful: exactly one status line, for the REMOVED serial — not the bare ip
    out = capsys.readouterr().out
    assert out.count("Disconnected:") == 1, out
    assert "Disconnected: 1.2.3.4:41234" in out, out


def test_no_disconnect_for_unselected_already_disconnected(capsys):
    devices = [
        ("1.2.3.4", "adb-X123-y0._adb-tls-connect._tcp.local.", False),
        ("1.2.3.5", "adb-X999-z9._adb-tls-connect._tcp.local.", False),
    ]
    with patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=devices), \
         patch('adb_wifi_connect.cli.select_devices', return_value=[1]), \
         patch('adb_wifi_connect.cli.connect_single_device',
               side_effect=lambda ip, **kw: f"{ip}:5555"), \
         patch('adb_wifi_connect.adb.run_adb_disconnect') as mock_disc, \
         patch('sys.argv', ['awc']):
        from adb_wifi_connect.cli import main
        main()
    mock_disc.assert_not_called()
    assert "Disconnected" not in capsys.readouterr().out


# --- sixel visibility: chosen QR mode logged under --debug ---

def test_display_qr_ascii_logs_mode(caplog):
    from adb_wifi_connect.qr import display_qr
    with caplog.at_level('DEBUG'):
        display_qr("WIFI:T:ADB;S:a;P:b;;", mode="ascii")
    messages = [r.getMessage() for r in caplog.records]
    assert any("QR mode" in m and "ascii" in m for m in messages), messages


def test_display_qr_auto_logs_probe_verdict(caplog):
    from adb_wifi_connect.qr import display_qr
    with caplog.at_level('DEBUG'), \
         patch('adb_wifi_connect.qr.supports_sixel', return_value=False):
        display_qr("WIFI:T:ADB;S:a;P:b;;", mode="auto")
    messages = [r.getMessage() for r in caplog.records]
    assert any("QR mode" in m and "ascii" in m for m in messages), messages


# --- QR centering ---

def test_display_qr_centers_horizontally(capsys):
    import os as _os
    from adb_wifi_connect.qr import display_qr
    with patch('adb_wifi_connect.qr.shutil.get_terminal_size',
               return_value=_os.terminal_size((200, 60))):
        display_qr("WIFI:T:ADB;S:a;P:b;;", mode="ascii")
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert lines, "no QR output"
    pads = {len(l) - len(l.lstrip()) for l in lines}
    assert len(pads) == 1, f"uneven horizontal padding: {pads}"
    assert next(iter(pads)) > 0, "QR not indented at all"


def test_display_qr_centers_vertically(capsys):
    import os as _os
    from adb_wifi_connect.qr import display_qr
    with patch('adb_wifi_connect.qr.shutil.get_terminal_size',
               return_value=_os.terminal_size((200, 60))):
        display_qr("WIFI:T:ADB;S:a;P:b;;", mode="ascii")
    out = capsys.readouterr().out
    assert out.startswith("\n"), "no vertical centering (leading blank lines)"


def test_display_qr_survives_tiny_terminal(capsys):
    import os as _os
    from adb_wifi_connect.qr import display_qr
    with patch('adb_wifi_connect.qr.shutil.get_terminal_size',
               return_value=_os.terminal_size((10, 3))):
        display_qr("WIFI:T:ADB;S:a;P:b;;", mode="ascii")  # must not raise
    out = capsys.readouterr().out
    assert out.strip(), "QR vanished on tiny terminal"


# --- paired/unpaired status column ---

def test_discover_returns_paired_flag():
    mock_proc = MagicMock()
    mock_proc.stdout = "List of devices attached\n\n"
    with patch('adb_wifi_connect.mdns.Zeroconf') as mock_zc_cls, \
         patch('adb_wifi_connect.mdns.ServiceBrowser') as mock_browser_cls, \
         patch('adb_wifi_connect.adb.subprocess.run',
               return_value=mock_proc):
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc
        info = MagicMock()
        info.server = "1.2.3.4"
        info.port = 37000
        info.name = "adb-X-y._adb-tls-connect._tcp.local."

        def fake_browser(zc, svc_type, listener):
            if "connect" in svc_type:
                listener.add_service(zc, svc_type, "svc")
            return MagicMock()

        mock_browser_cls.side_effect = fake_browser
        with patch.object(mock_zc, 'get_service_info', return_value=info):
            from adb_wifi_connect.mdns import discover_all_phones
            result = discover_all_phones(timeout=5, grace=0.05)

    # 4th element: seen advertising the CONNECT service => paired
    assert len(result[0]) >= 4
    assert result[0][3] is True


def test_discover_unpaired_when_only_pairing_service_seen():
    mock_proc = MagicMock()
    mock_proc.stdout = "List of devices attached\n\n"
    with patch('adb_wifi_connect.mdns.Zeroconf') as mock_zc_cls, \
         patch('adb_wifi_connect.mdns.ServiceBrowser') as mock_browser_cls, \
         patch('adb_wifi_connect.adb.subprocess.run',
               return_value=mock_proc):
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc
        info = MagicMock()
        info.server = "1.2.3.4"
        info.port = 37000
        info.name = "adb-X-y._adb-tls-pairing._tcp.local."

        def fake_browser(zc, svc_type, listener):
            if "pairing" in svc_type:
                listener.add_service(zc, svc_type, "svc")
            return MagicMock()

        mock_browser_cls.side_effect = fake_browser
        with patch.object(mock_zc, 'get_service_info', return_value=info):
            from adb_wifi_connect.mdns import discover_all_phones
            result = discover_all_phones(timeout=5, grace=0.05)

    assert len(result[0]) >= 4
    assert result[0][3] is False


def test_device_list_status_priority(capsys):
    from adb_wifi_connect.ui import _print_device_list
    devices = [
        ("1.2.3.4", "A", True, True),    # connected wins
        ("1.2.3.5", "B", False, True),   # paired, not connected
        ("1.2.3.6", "C", False, False),  # unpaired
        ("1.2.3.7", "D", False),         # legacy 3-tuple
    ]
    _print_device_list(devices)
    out = capsys.readouterr().out
    # new contract: 'ip short_name' label (no identity yet), status after
    # three spaces — the old 'name (ip) - status' shape is gone
    assert "A (1.2.3.4)" not in out
    assert "  1. 1.2.3.4 A   Connected" in out
    assert "  2. 1.2.3.5 B   Paired" in out
    assert "  3. 1.2.3.6 C   Unpaired" in out
    assert "  4. 1.2.3.7 D   Not connected" in out  # unknown stays generic
