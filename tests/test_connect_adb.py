import sys
import os

import pytest

from adb_wifi_connect.connect import generate_code, generate_device_name, format_qr_data


def test_generate_code_length():
    code = generate_code(6)
    assert len(code) == 6


def test_generate_code_alphanumeric():
    code = generate_code(10)
    assert code.isalnum()


def test_generate_code_random():
    codes = {generate_code(6) for _ in range(50)}
    assert len(codes) > 1  # at least some should differ


def test_generate_device_name_default_prefix():
    name = generate_device_name()
    assert name.startswith("debug_")


def test_generate_device_name_custom_prefix():
    name = generate_device_name("myapp")
    assert name.startswith("myapp_")


def test_format_qr_data():
    result = format_qr_data("debug", "123456")
    assert result == "WIFI:T:ADB;S:debug;P:123456;;"


def test_format_qr_data_different_values():
    result = format_qr_data("phone", "abcdef")
    assert result == "WIFI:T:ADB;S:phone;P:abcdef;;"


from unittest.mock import patch, MagicMock
from adb_wifi_connect.connect import display_qr


def test_display_qr_calls_qrcode():
    """display_qr should use qrcode library when available."""
    with patch('adb_wifi_connect.qr.qrcode') as mock_qr:
        mock_qr_code = MagicMock()
        mock_qr.QRCode.return_value = mock_qr_code
        mock_qr_code.print_ascii = MagicMock()

        display_qr("WIFI:T:ADB;S:test;P:123;;")

        mock_qr.QRCode.assert_called_once()
        mock_qr_code.add_data.assert_called_once_with("WIFI:T:ADB;S:test;P:123;;")
        mock_qr_code.make.assert_called_once_with(fit=True)
        mock_qr_code.print_ascii.assert_called_once_with(invert=True)


def test_display_qr_fallback_without_qrcode():
    """display_qr should print text if qrcode not installed."""
    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == 'qrcode':
            raise ImportError("No module named 'qrcode'")
        return real_import(name, *args, **kwargs)

    with patch.object(builtins, '__import__', side_effect=mock_import):
        # Should not raise, should print fallback
        display_qr("WIFI:T:ADB;S:test;P:123;;")


def test_render_ascii_qr_lines_returns_unpadded_lines():
    pytest.importorskip("qrcode")  # optional extra: skip, not fail, when absent
    from adb_wifi_connect.qr import render_ascii_qr_lines, _display_qr_ascii
    lines = render_ascii_qr_lines("WIFI:T:ADB;S:debug_ab12;P:cd34ef;;")
    assert lines and all(isinstance(l, str) for l in lines)
    # raw: no centering pad — first line starts with a QR glyph, not a space
    assert lines[0].strip() == lines[0]


def test_render_ascii_qr_lines_empty_without_qrcode():
    from adb_wifi_connect.qr import render_ascii_qr_lines
    with patch('adb_wifi_connect.qr.qrcode', None):
        assert render_ascii_qr_lines("WIFI:T:ADB;S:debug_ab12;P:cd34ef;;") == []


def test_display_qr_ascii_centering_unchanged():
    """_display_qr_ascii output equals pre-change golden: print_ascii capture + terminal centering."""
    pytest.importorskip("qrcode")  # optional extra: skip, not fail, when absent
    import io
    import shutil
    import contextlib
    from adb_wifi_connect import qr as qr_module

    text = "WIFI:T:ADB;S:debug_ab12;P:cd34ef;;"
    # rebuild golden via the same qrcode.print_ascii capture the function uses
    golden = qr_module.qrcode.QRCode(
        version=1,
        error_correction=qr_module.qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=1,
    )
    golden.add_data(text)
    golden.make(fit=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        golden.print_ascii(invert=True)
    lines = buf.getvalue().splitlines()
    assert lines  # golden capture must have content

    term = shutil.get_terminal_size(fallback=(80, 24))
    width = max(len(line) for line in lines)
    pad = max(0, (term.columns - width) // 2)
    vpad = max(0, (term.lines - len(lines)) // 2)
    expected = "\n" * vpad + "\n".join(" " * pad + line for line in lines)

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        qr_module._display_qr_ascii(text)
    assert out.getvalue() == expected + "\n"


from adb_wifi_connect.connect import is_device_connected, run_adb_pair, run_adb_connect


def test_is_device_connected_true():
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(
            stdout="List of devices attached\n192.168.1.170:5555 device\n",
            returncode=0
        )
        assert is_device_connected("192.168.1.170") == 5555


def test_is_device_connected_false():
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(
            stdout="List of devices attached\n\n",
            returncode=0
        )
        assert is_device_connected("192.168.1.170") is None


def test_run_adb_pair_success():
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(
            stdout="Successfully paired to 192.168.1.170:37000",
            returncode=0
        )
        assert run_adb_pair("192.168.1.170", 37000, "123456") is True


def test_run_adb_pair_failure():
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(
            stdout="Failed: Unable to start pairing client.",
            returncode=1
        )
        assert run_adb_pair("192.168.1.170", 37000, "wrong") is False


def test_run_adb_connect_success():
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(
            stdout="connected to 192.168.1.170:5555",
            returncode=0
        )
        assert run_adb_connect("192.168.1.170", 5555) is True


def test_run_adb_connect_failure():
    with patch('subprocess.run') as mock_run:
        mock_run.return_value = MagicMock(
            stdout="cannot connect to 192.168.1.170:5555",
            returncode=1
        )
        assert run_adb_connect("192.168.1.170", 5555) is False


from adb_wifi_connect.adb import run_adb_disconnect


def test_disconnect_removes_mdns_serial():
    """Truthful disconnect: candidates come from one live `adb devices`
    snapshot (ip:port form + exact mDNS-form serial), every `adb disconnect`
    argv targets the EXACT serial (a bare ip makes adb misparse ip:5555),
    and only adb-confirmed removals are returned."""
    devices_out = (
        "List of devices attached\n"
        "192.168.1.19:41234\tdevice\n"
        "adb-X123-y0._adb-tls-connect._tcp\tdevice\n"
        "1.2.3.4:5555\tdevice\n"
        "\n"
    )

    def fake_run(argv, **kw):
        assert isinstance(argv, list), f"expected argv list, got {argv!r}"
        if argv[1] == "devices":
            return MagicMock(stdout=devices_out, returncode=0)
        # adb echoes the serial it actually removed
        return MagicMock(stdout=f"disconnected {argv[2]}", returncode=0)

    with patch('adb_wifi_connect.adb.subprocess.run',
               side_effect=fake_run) as mock_run:
        removed = run_adb_disconnect("192.168.1.19", "X123-y0")

    # both live serial forms for this ip removed; the other device untouched
    assert set(removed) == {
        "192.168.1.19:41234",
        "adb-X123-y0._adb-tls-connect._tcp",
    }
    disc_calls = [c for c in mock_run.call_args_list
                  if c[0][0][1] == "disconnect"]
    assert [c[0][0] for c in disc_calls] == [
        ["adb", "disconnect", "192.168.1.19:41234"],
        ["adb", "disconnect", "adb-X123-y0._adb-tls-connect._tcp"],
    ]
    for c in disc_calls:
        assert c[1].get("timeout") == 5
        assert "shell" not in c[1]
    # exactly ONE devices parser run (no second snapshot)
    devices_calls = [c for c in mock_run.call_args_list
                     if c[0][0][1] == "devices"]
    assert len(devices_calls) == 1


def test_disconnect_failure_prints_nothing(capsys):
    """A failure is never reported as a removal: adb's error output keeps
    the serial out of the returned list, zero live serials runs nothing,
    and the CLI desired-state loop prints no 'Disconnected:' line on an
    empty return (Review Focus #5)."""
    # (1) adb refuses the disconnect -> serial NOT in the returned list
    live_one = "List of devices attached\n1.2.3.4:5555\tdevice\n\n"
    with patch('adb_wifi_connect.adb.subprocess.run',
               side_effect=[
                   MagicMock(stdout=live_one, returncode=0),
                   MagicMock(stdout="error: no such device '1.2.3.4:5555'",
                             returncode=1),
               ]) as mock_run:
        removed = run_adb_disconnect("1.2.3.4")
    assert removed == []
    disc_calls = [c for c in mock_run.call_args_list
                  if c[0][0][1] == "disconnect"]
    assert len(disc_calls) == 1
    assert disc_calls[0][0][0] == ["adb", "disconnect", "1.2.3.4:5555"]

    # (2) zero candidates -> [] and nothing run beyond the snapshot
    with patch('adb_wifi_connect.adb.subprocess.run',
               return_value=MagicMock(
                   stdout="List of devices attached\n\n",
                   returncode=0)) as mock_run:
        assert run_adb_disconnect("1.2.3.4", "X123-y0") == []
    assert [c[0][0] for c in mock_run.call_args_list] == [
        ["adb", "devices"]]

    # (3) cli desired-state loop: empty return -> no 'Disconnected:' line
    devices = [
        ("1.2.3.4", "X123-y0", True),
        ("1.2.3.5", "X999-z9", False),
    ]
    capsys.readouterr()  # start from a clean buffer: assert on CLI output only
    with patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=devices), \
         patch('adb_wifi_connect.cli.select_devices', return_value=[1]), \
         patch('adb_wifi_connect.cli.connect_single_device',
               side_effect=lambda ip, **kw: f"{ip}:5555"), \
         patch('adb_wifi_connect.adb.run_adb_disconnect',
               return_value=[]) as mock_disc, \
         patch('sys.argv', ['awc']):
        from adb_wifi_connect.cli import main
        main()
    mock_disc.assert_called_once_with("1.2.3.4", "X123-y0")
    out = capsys.readouterr().out
    assert "Disconnected" not in out, out


from adb_wifi_connect.connect import discover_pairing_service


def test_discover_pairing_service_found():
    with patch('adb_wifi_connect.mdns.Zeroconf') as mock_zc_cls, \
         patch('adb_wifi_connect.mdns.ServiceBrowser') as mock_browser_cls:
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc

        mock_info = MagicMock()
        mock_info.server = "192.168.1.170"
        mock_info.port = 37000

        # Simulate immediate discovery
        def fake_browser(zc, svc_type, listener):
            listener.add_service(zc, svc_type, "test_service")
            return MagicMock()

        mock_browser_cls.side_effect = fake_browser

        with patch.object(mock_zc, 'get_service_info', return_value=mock_info):
            with patch.object(mock_zc, 'close'):
                result = discover_pairing_service(timeout=5)

        assert result == ("192.168.1.170", 37000)


def test_discover_pairing_service_timeout():
    with patch('adb_wifi_connect.mdns.Zeroconf') as mock_zc_cls, \
         patch('adb_wifi_connect.mdns.ServiceBrowser') as mock_browser_cls:
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc

        # Never calls add_service — timeout
        mock_browser_cls.return_value = MagicMock()

        with patch.object(mock_zc, 'close'):
            result = discover_pairing_service(timeout=1)

        assert result is None


from adb_wifi_connect.connect import ask_pairing_code, ask_pairing_port, ask_connection_port


def test_ask_pairing_code():
    with patch('builtins.input', return_value='123456'):
        assert ask_pairing_code() == '123456'


def test_ask_pairing_code_strips():
    with patch('builtins.input', return_value='  654321  '):
        assert ask_pairing_code() == '654321'


def test_ask_pairing_port():
    with patch('builtins.input', return_value='37000'):
        assert ask_pairing_port() == 37000


def test_ask_pairing_port_invalid_then_valid():
    with patch('builtins.input', side_effect=['abc', '37000']):
        assert ask_pairing_port() == 37000


def test_ask_connection_port():
    with patch('builtins.input', return_value='5555'):
        assert ask_connection_port() == 5555


# --- paired-skip, live-transport reuse, no-countdown wait (spec §5) ---

def test_connect_single_device_paired_skips_wait(capsys):
    """paired=True never enters the pairing branch: no session, no QR
    wait, no manual prompts — straight to the connection."""
    with patch('adb_wifi_connect.flow.is_device_connected',
               return_value=None), \
         patch('adb_wifi_connect.flow.sys.stdin.isatty',
               return_value=True), \
         patch('adb_wifi_connect.flow.start_pairing_session') as mock_start, \
         patch('adb_wifi_connect.flow.wait_for_scanned_event') as mock_wait, \
         patch('adb_wifi_connect.flow.ask_pairing_code') as mock_code, \
         patch('adb_wifi_connect.flow.ask_pairing_port') as mock_pport, \
         patch('adb_wifi_connect.flow.run_adb_pair') as mock_pair, \
         patch('adb_wifi_connect.flow.discover_connection_port',
               return_value=(None, None)), \
         patch('adb_wifi_connect.flow.scan_for_adb_port',
               return_value=5555):
        from adb_wifi_connect.connect import connect_single_device
        result = connect_single_device("1.2.3.4", paired=True)
    assert result == "1.2.3.4:5555"
    mock_start.assert_not_called()
    mock_wait.assert_not_called()
    mock_code.assert_not_called()
    mock_pport.assert_not_called()
    mock_pair.assert_not_called()
    out = capsys.readouterr().out
    assert "PAIRING" not in out, out
    assert "Waiting for" not in out, out


def test_connect_single_device_reuses_live_mdns_transport():
    """A phone already live under its mDNS-form serial needs no second
    transport: verify it answers `shell echo test`, then reuse it —
    no adb connect, no discovery, no port scan (spec §5)."""
    short = "A1B2C3D4E5F6-xyz123"
    serial = f"adb-{short}._adb-tls-connect._tcp"
    with patch('adb_wifi_connect.flow.is_device_connected',
               return_value=None), \
         patch('adb_wifi_connect.flow.sys.stdin.isatty',
               return_value=True), \
         patch('adb_wifi_connect.flow.start_pairing_session') as mock_start, \
         patch('adb_wifi_connect.flow.wait_for_scanned_event') as mock_wait, \
         patch('adb_wifi_connect.adb.connected_serials',
               return_value={serial}) as mock_serials, \
         patch('adb_wifi_connect.flow.subprocess.run',
               return_value=MagicMock(returncode=0)) as mock_run, \
         patch('adb_wifi_connect.flow.run_adb_connect') as mock_connect, \
         patch('adb_wifi_connect.flow.connect_with_retries') as mock_retries, \
         patch('adb_wifi_connect.flow.discover_connection_port') as mock_disc, \
         patch('adb_wifi_connect.flow.scan_for_adb_port') as mock_scan:
        from adb_wifi_connect.adb import ADB_PATH
        from adb_wifi_connect.connect import connect_single_device
        result = connect_single_device("1.2.3.4", paired=True,
                                       short_name=short,
                                       connect_port=37000)
    assert result == "1.2.3.4:37000"
    # pairing never entered (paired=True)
    mock_start.assert_not_called()
    mock_wait.assert_not_called()
    # the live serial was verified with the is_device_connected echo pattern
    mock_serials.assert_called_once_with()
    mock_run.assert_called_once_with(
        [ADB_PATH, "-s", serial, "shell", "echo", "test"],
        capture_output=True, text=True, timeout=5)
    # no duplicate transport, no port work
    mock_connect.assert_not_called()
    mock_retries.assert_not_called()
    mock_disc.assert_not_called()
    mock_scan.assert_not_called()


def test_connect_single_device_qr_wait_printes_no_countdown(capsys):
    """The unpaired QR wait announces exactly ONE hint — no countdown,
    no window number, no timeout strings anywhere in the output."""
    session = MagicMock()
    with patch('adb_wifi_connect.flow.is_device_connected',
               return_value=None), \
         patch('adb_wifi_connect.flow.sys.stdin.isatty',
               return_value=True), \
         patch('adb_wifi_connect.flow.start_pairing_session',
               return_value=session) as mock_start, \
         patch('adb_wifi_connect.flow.wait_for_scanned_event',
               return_value="manual") as mock_wait, \
         patch('adb_wifi_connect.flow.ask_pairing_code',
               return_value="123456"), \
         patch('adb_wifi_connect.flow.ask_pairing_port',
               return_value=37000), \
         patch('adb_wifi_connect.flow.run_adb_pair',
               return_value=True), \
         patch('adb_wifi_connect.flow.discover_connection_port',
               return_value=(None, None)), \
         patch('adb_wifi_connect.flow.scan_for_adb_port',
               return_value=5555):
        from adb_wifi_connect.connect import connect_single_device
        result = connect_single_device("1.2.3.4", skip_pair=False,
                                       manual=False)
    assert result == "1.2.3.4:5555"
    mock_start.assert_called_once()
    mock_wait.assert_called_once()
    # no explicit timeout handed to the wait — it is timerless now
    assert "timeout" not in mock_wait.call_args.kwargs
    out = capsys.readouterr().out
    assert ("Waiting for the QR scan — press Enter for manual mode, "
            "Ctrl+C to quit") in out, out
    assert "(Press Enter for manual mode)" not in out, out  # one hint only
    assert "30s" not in out, out
    assert "s timeout" not in out, out
    assert "QR timeout" not in out, out
