import sys
import os
import queue

from unittest.mock import patch, MagicMock, call
import threading
import io


def test_discover_all_phones_empty():
    """No devices found returns empty list."""
    with patch('adb_wifi_connect.mdns.Zeroconf') as mock_zc_cls, \
         patch('adb_wifi_connect.mdns.ServiceBrowser') as mock_browser_cls:
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc
        mock_browser_cls.return_value = MagicMock()

        with patch.object(mock_zc, 'close'):
            from adb_wifi_connect.connect import discover_all_phones
            result = discover_all_phones(timeout=1, grace=0.05)

    assert result == []


def test_discover_all_phones_single():
    """Single device found returns list with one tuple."""
    mock_proc = MagicMock()
    mock_proc.stdout = "List of devices attached\n\n"

    with patch('adb_wifi_connect.mdns.Zeroconf') as mock_zc_cls, \
         patch('adb_wifi_connect.mdns.ServiceBrowser') as mock_browser_cls, \
         patch('adb_wifi_connect.adb.subprocess.run', return_value=mock_proc):
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc

        mock_info = MagicMock()
        mock_info.server = "192.168.1.167"
        mock_info.port = 37000

        def fake_browser(zc, svc_type, listener):
            listener.add_service(zc, svc_type, "test_service")
            return MagicMock()

        mock_browser_cls.side_effect = fake_browser

        with patch.object(mock_zc, 'get_service_info', return_value=mock_info):
            with patch.object(mock_zc, 'close'):
                from adb_wifi_connect.connect import discover_all_phones
                result = discover_all_phones(timeout=5, grace=0.05)

    assert len(result) == 1
    assert result[0][0] == "192.168.1.167"
    assert result[0][2] is False  # not connected


def test_discover_all_phones_multiple():
    """Multiple devices found returns all."""
    mock_proc = MagicMock()
    mock_proc.stdout = "List of devices attached\n\n"

    with patch('adb_wifi_connect.mdns.Zeroconf') as mock_zc_cls, \
         patch('adb_wifi_connect.mdns.ServiceBrowser') as mock_browser_cls, \
         patch('adb_wifi_connect.adb.subprocess.run', return_value=mock_proc):
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc

        call_count = [0]
        devices = [
            ("192.168.1.167", 37000),
            ("192.168.1.172", 37001),
        ]

        def fake_browser(zc, svc_type, listener):
            if call_count[0] < len(devices):
                ip, port = devices[call_count[0]]
                mock_info = MagicMock()
                mock_info.server = ip
                mock_info.port = port
                with patch.object(zc, 'get_service_info', return_value=mock_info):
                    listener.add_service(zc, svc_type, f"service_{call_count[0]}")
                call_count[0] += 1
            return MagicMock()

        mock_browser_cls.side_effect = fake_browser

        with patch.object(mock_zc, 'close'):
            from adb_wifi_connect.connect import discover_all_phones
            result = discover_all_phones(timeout=5, grace=0.05)

    assert len(result) == 2
    ips = {r[0] for r in result}
    assert "192.168.1.167" in ips
    assert "192.168.1.172" in ips


def test_discover_all_phones_dedup():
    """Same IP on both service types is deduplicated."""
    mock_proc = MagicMock()
    mock_proc.stdout = "List of devices attached\n\n"

    with patch('adb_wifi_connect.mdns.Zeroconf') as mock_zc_cls, \
         patch('adb_wifi_connect.mdns.ServiceBrowser') as mock_browser_cls, \
         patch('adb_wifi_connect.adb.subprocess.run', return_value=mock_proc):
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc

        mock_info = MagicMock()
        mock_info.server = "192.168.1.167"
        mock_info.port = 37000

        def fake_browser(zc, svc_type, listener):
            listener.add_service(zc, svc_type, "test_service")
            return MagicMock()

        mock_browser_cls.side_effect = fake_browser

        with patch.object(mock_zc, 'get_service_info', return_value=mock_info):
            with patch.object(mock_zc, 'close'):
                from adb_wifi_connect.connect import discover_all_phones
                result = discover_all_phones(timeout=5, grace=0.05)

    # Both browsers fire for same IP, should deduplicate
    assert len(result) == 1
    assert result[0][0] == "192.168.1.167"


def test_discover_all_phones_connected():
    """Already-connected devices marked correctly."""
    mock_proc = MagicMock()
    mock_proc.stdout = "List of devices attached\n192.168.1.167:5555\tdevice\n"

    with patch('adb_wifi_connect.mdns.Zeroconf') as mock_zc_cls, \
         patch('adb_wifi_connect.mdns.ServiceBrowser') as mock_browser_cls, \
         patch('adb_wifi_connect.adb.subprocess.run', return_value=mock_proc):
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc

        mock_info = MagicMock()
        mock_info.server = "192.168.1.167"
        mock_info.port = 37000

        def fake_browser(zc, svc_type, listener):
            listener.add_service(zc, svc_type, "test_service")
            return MagicMock()

        mock_browser_cls.side_effect = fake_browser

        with patch.object(mock_zc, 'get_service_info', return_value=mock_info):
            with patch.object(mock_zc, 'close'):
                from adb_wifi_connect.connect import discover_all_phones
                result = discover_all_phones(timeout=5, grace=0.05)

    assert len(result) == 1
    assert result[0][2] is True  # is_connected


def test_discover_all_phones_no_zeroconf():
    """Returns empty list when zeroconf not installed."""
    with patch('adb_wifi_connect.mdns.Zeroconf', None):
        from adb_wifi_connect.connect import discover_all_phones
        result = discover_all_phones(timeout=1, grace=0.05)
    assert result == []


def test_discover_records_carry_connect_port_and_identity_slot():
    """Records are 6-tuples (ip, name, is_connected, is_paired, connect_port,
    identity): connect_port comes from the _adb-tls-connect record (None when
    only the pairing service was seen); identity is a reserved slot, always
    None at discovery."""
    from adb_wifi_connect.connect import (discover_all_phones,
                                          MDNS_CONNECT_TYPE, MDNS_TYPE)
    mock_proc = MagicMock()
    mock_proc.stdout = "List of devices attached\n\n"

    with patch('adb_wifi_connect.mdns.Zeroconf') as mock_zc_cls, \
         patch('adb_wifi_connect.mdns.ServiceBrowser') as mock_browser_cls, \
         patch('adb_wifi_connect.adb.subprocess.run', return_value=mock_proc):
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc

        def make_info(ip, name, port):
            info = MagicMock()
            info.server = ip
            info.name = name
            info.port = port
            return info

        # one device per service type: the connect-service device carries
        # a connect port, the pairing-only device does not
        infos = {
            "svc-connect": (MDNS_CONNECT_TYPE, make_info(
                "192.168.1.167", "adb-X123-y0._adb-tls-connect._tcp.local.",
                41123)),
            "svc-pair": (MDNS_TYPE, make_info(
                "192.168.1.172", "adb-Y999-z9._adb-tls-pairing._tcp.local.",
                37777)),
        }

        def get_info(svc_type, name):
            expected_type, info = infos[name]
            assert svc_type == expected_type
            return info

        def fake_browser(zc, svc_type, listener):
            for name, (expected_type, _info) in infos.items():
                if svc_type == expected_type:
                    listener.add_service(zc, svc_type, name)
            return MagicMock()

        mock_browser_cls.side_effect = fake_browser
        with patch.object(mock_zc, 'get_service_info', side_effect=get_info):
            result = discover_all_phones(timeout=5, grace=0.05)

    by_ip = {rec[0]: rec for rec in result}
    assert set(by_ip) == {"192.168.1.167", "192.168.1.172"}
    connect_rec = by_ip["192.168.1.167"]
    assert len(connect_rec) == 6
    assert connect_rec[4] == 41123
    assert connect_rec[5] is None
    pairing_rec = by_ip["192.168.1.172"]
    assert len(pairing_rec) == 6
    assert pairing_rec[4] is None
    assert pairing_rec[5] is None


def test_connected_status_recognizes_mdns_form_serial():
    """Devices connected over TLS show up in `adb devices` under the mDNS
    service form (adb-<name>._adb-tls-connect._tcp), not ip:port — a live
    serial in that form must mark the matching record as connected."""
    with patch('adb_wifi_connect.mdns.Zeroconf') as mock_zc_cls, \
         patch('adb_wifi_connect.mdns.ServiceBrowser') as mock_browser_cls, \
         patch('adb_wifi_connect.mdns.connected_serials',
               return_value={'adb-A1B2C3D4E5F6-xyz123._adb-tls-connect._tcp'}):
        mock_zc = MagicMock()
        mock_zc_cls.return_value = mock_zc
        info = MagicMock()
        info.server = "192.168.1.19"
        info.port = 37000
        info.name = "adb-A1B2C3D4E5F6-xyz123._adb-tls-connect._tcp.local."

        def fake_browser(zc, svc_type, listener):
            listener.add_service(zc, svc_type, "svc")
            return MagicMock()

        mock_browser_cls.side_effect = fake_browser
        with patch.object(mock_zc, 'get_service_info', return_value=info):
            from adb_wifi_connect.connect import discover_all_phones
            result = discover_all_phones(timeout=5, grace=0.05)

    assert len(result) == 1
    assert result[0][1] == "A1B2C3D4E5F6-xyz123"
    assert result[0][2] is True


def test_connected_serials_includes_mdns_form_serials():
    """connected_serials() returns every serial in state `device` from one
    `adb devices` run — both ip:port and mDNS-form (TLS) serials — and an
    empty set when adb cannot be run."""
    from adb_wifi_connect.adb import connected_serials
    mock_proc = MagicMock()
    mock_proc.stdout = (
        "List of devices attached\n"
        "192.168.1.167:5555\tdevice\n"
        "adb-A1B2C3D4E5F6-xyz123._adb-tls-connect._tcp\tdevice\n"
        "0123456789ABCDEF\toffline\n"
    )
    with patch('adb_wifi_connect.adb.subprocess.run', return_value=mock_proc):
        serials = connected_serials()
    assert serials == {
        "192.168.1.167:5555",
        "adb-A1B2C3D4E5F6-xyz123._adb-tls-connect._tcp",
    }

    with patch('adb_wifi_connect.adb.subprocess.run',
               side_effect=OSError("no adb")):
        assert connected_serials() == set()


def test_select_devices_single_device():
    """Single device auto-selected, no UI."""
    devices = [("192.168.1.167", "Pixel", False)]
    from adb_wifi_connect.connect import select_devices
    result = select_devices(devices, is_tty=False)
    assert result == [0]


def test_select_devices_noninteractive_numbers():
    """Non-interactive: parse comma-separated numbers."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", True),
    ]
    with patch('builtins.input', return_value='1,2'):
        from adb_wifi_connect.connect import select_devices
        result = select_devices(devices, is_tty=False)
    assert result == [0, 1]


def test_select_devices_noninteractive_all():
    """Non-interactive: 'all' selects everything."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", True),
        ("192.168.1.173", "OnePlus", False),
    ]
    with patch('builtins.input', return_value='all'):
        from adb_wifi_connect.connect import select_devices
        result = select_devices(devices, is_tty=False)
    assert result == [0, 1, 2]


def test_select_devices_noninteractive_single_number():
    """Non-interactive: single number."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", True),
    ]
    with patch('builtins.input', return_value='2'):
        from adb_wifi_connect.connect import select_devices
        result = select_devices(devices, is_tty=False)
    assert result == [1]


def test_select_devices_noninteractive_invalid():
    """Non-interactive: invalid input returns empty."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", True),
    ]
    with patch('builtins.input', return_value='abc'):
        from adb_wifi_connect.connect import select_devices
        result = select_devices(devices, is_tty=False)
    assert result == []


def test_select_devices_noninteractive_out_of_range():
    """Non-interactive: out of range numbers ignored."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", True),
    ]
    with patch('builtins.input', return_value='1,5,2'):
        from adb_wifi_connect.connect import select_devices
        result = select_devices(devices, is_tty=False)
    assert result == [0, 1]


def test_select_devices_single_device_no_print():
    """Single device: auto-selected with no list printed."""
    devices = [("192.168.1.167", "Pixel", False)]
    from adb_wifi_connect.connect import select_devices
    with patch('builtins.print') as mock_print:
        result = select_devices(devices, is_tty=False)
    assert result == [0]
    mock_print.assert_not_called()


def test_select_devices_interactive_calls_curses():
    """Interactive mode: delegates to curses selection with the QR
    payload and session threaded through (None when there is no QR)."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", True),
    ]
    with patch('adb_wifi_connect.ui._curses_selection', return_value=[0, 1]) as mock_curses:
        from adb_wifi_connect.connect import select_devices
        result = select_devices(devices, is_tty=True)
    mock_curses.assert_called_once_with(devices, qr_text=None, session=None)
    assert result == [0, 1]


def test_select_devices_signature_passes_qr_and_session():
    """select_devices threads qr_text/session down to the curses
    selector, and cli.main passes session.qr_data + the session."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", False),
    ]
    session = MagicMock()
    session.events = queue.Queue()
    session.qr_data = "WIFI:T:ADB;S:debug_ab12;P:cd34ef;;"

    # ui level: qr_text and session ride through to the curses selector
    with patch('adb_wifi_connect.ui._curses_selection',
               return_value=[0]) as mock_curses:
        from adb_wifi_connect.connect import select_devices
        select_devices(devices, is_tty=True, qr_text=session.qr_data,
                       session=session)
    mock_curses.assert_called_once_with(devices, qr_text=session.qr_data,
                                        session=session)

    # cli level: the live session's QR payload is what gets passed
    with patch('adb_wifi_connect.cli.start_pairing_session',
               return_value=session), \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=list(devices)), \
         patch('adb_wifi_connect.cli.enrich_devices', side_effect=list), \
         patch('adb_wifi_connect.cli.select_devices',
               return_value=[0]) as mock_select, \
         patch('adb_wifi_connect.cli.connect_single_device',
               return_value="192.168.1.167:5555"), \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('sys.stdin.isatty', return_value=True), \
         patch('sys.argv', ['awc']):
        from adb_wifi_connect.cli import main
        main()
    kwargs = mock_select.call_args.kwargs
    assert kwargs.get("qr_text") == "WIFI:T:ADB;S:debug_ab12;P:cd34ef;;"
    assert kwargs.get("session") is session
    assert kwargs.get("is_tty") is True


def test_curses_selection_draws_qr_lines_above_list():
    """QR lines render flush-left above the device list, in the exact
    redraw order, when the fit check passes."""
    devices = [("192.168.1.167", "Pixel", False)]
    qr_lines = ["### ###", " # # # ", "### ###"]
    rows = []

    def fake_wrapper(main_func):
        keys = [10]  # Enter → confirm

        class FakeStdscr:
            def clear(self): pass
            def addstr(self, s): rows.append(s)
            def refresh(self): pass
            def timeout(self, ms): pass
            def getch(self): return keys.pop(0)

        return main_func(FakeStdscr())

    with patch('curses.wrapper', side_effect=fake_wrapper), \
         patch('curses.curs_set'), \
         patch('adb_wifi_connect.ui.render_ascii_qr_lines',
               return_value=qr_lines), \
         patch('adb_wifi_connect.ui.shutil.get_terminal_size',
               return_value=os.terminal_size((80, 40))):
        from adb_wifi_connect.ui import _curses_selection
        result = _curses_selection(devices, qr_text="WIFI:T:ADB;S:x;P:y;;")

    assert result == []
    assert "".join(rows) == (
        "=== ADB WiFi Connection ===\n"
        "\n"
        "### ###\n"
        " # # # \n"
        "### ###\n"
        "\n"
        "Found 1 devices:\n"
        "\n"
        "> [ ] 1. 192.168.1.167 Pixel   Not connected\n"
        "\n"
        "  ↑/↓ move · Space toggle · Enter connect · scan the QR to add "
        "· Ctrl+C quit\n"
    )


def test_curses_selection_small_terminal_omits_qr():
    """QR never renders partially: too small a terminal drops the whole
    block, and the keys still drive the selection."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", False),
    ]
    qr_lines = ["Q" * 9 for _ in range(7)]  # 7 + 2 + 8 = 17 > 10 lines
    rows = []

    def fake_wrapper(main_func):
        keys = [ord(' '), 10]  # toggle device 0 → Enter

        class FakeStdscr:
            def clear(self): pass
            def addstr(self, s): rows.append(s)
            def refresh(self): pass
            def timeout(self, ms): pass
            def getch(self): return keys.pop(0)

        return main_func(FakeStdscr())

    with patch('curses.wrapper', side_effect=fake_wrapper), \
         patch('curses.curs_set'), \
         patch('adb_wifi_connect.ui.render_ascii_qr_lines',
               return_value=qr_lines), \
         patch('adb_wifi_connect.ui.shutil.get_terminal_size',
               return_value=os.terminal_size((80, 10))):
        from adb_wifi_connect.ui import _curses_selection
        result = _curses_selection(devices, qr_text="WIFI:T:ADB;S:x;P:y;;")

    frame = "".join(rows)
    assert all(line not in frame for line in qr_lines), frame
    assert "Found 2 devices:" in frame
    assert result == [0]


def _run_fitted_selection(devices, height, keys, qr_lines, session=None,
                          width=80):
    """Run the selector through _run_interactive_selection (the real
    production entrypoint) against a stdscr double that RAISES
    curses.error exactly where real ncurses would. With all-'\\n'
    payloads (every payload production writes ends in a newline) real
    ncurses ERRs when the drawn content lines REACH the terminal
    height — the k-th newline lands on row k-1, which is the bottom
    row when k == height, and scrollok is off — so drawing succeeds
    only at height >= content lines + 1 (verified against real curses
    in a pty: 16/16 rows -> ERR, 15/16 -> OK). The helper therefore
    raises the moment cumulative newlines reach `height`
    (state["rows"] >= height) — the exact production failure mode
    (addstr on the bottom row) the fit check exists to prevent.
    `width` stubs the terminal COLUMNS (the fit check's width term).
    Returns (result, frames): the frames drawn, one list of addstr
    payloads per clear(). `input` is pinned to fail, so any fallback
    to the comma prompt fails the test loudly instead of passing
    silently.
    """
    import curses
    frames = []

    def fake_wrapper(main_func):
        key_iter = list(keys)
        state = {"rows": 0}

        class FittedStdscr:
            def clear(self):
                frames.append([])
                state["rows"] = 0

            def addstr(self, s):
                frames[-1].append(s)
                state["rows"] += s.count("\n")
                # Real curses ERRs when drawn lines REACH the height
                # (a newline issued on the bottom row), not one past it.
                if state["rows"] >= height:
                    raise curses.error("addstr() returned ERR")

            def refresh(self): pass
            def timeout(self, ms): pass
            def getch(self): return key_iter.pop(0)

        return main_func(FittedStdscr())

    with patch('curses.wrapper', side_effect=fake_wrapper), \
         patch('curses.curs_set'), \
         patch('builtins.input',
               side_effect=AssertionError("fit check failed -> comma-prompt "
                                          "fallback instead of list-only "
                                          "degradation")), \
         patch('adb_wifi_connect.ui.render_ascii_qr_lines',
               return_value=qr_lines), \
         patch('adb_wifi_connect.ui.shutil.get_terminal_size',
               return_value=os.terminal_size((width, height))):
        from adb_wifi_connect.ui import _run_interactive_selection
        result = _run_interactive_selection(devices,
                                            qr_text="WIFI:T:ADB;S:x;P:y;;",
                                            session=session)
    return result, frames


def test_curses_selection_blind_window_height_omits_qr_list_still_runs():
    """Former blind heights qr+devices+4 … qr+devices+7 (the old `+4`
    window, plus round 1's off-by-one `+7` where the check passed but
    real curses ERRed): at EVERY one of them the check must FAIL — real
    curses needs qr+devices+8 — so the QR is dropped whole and the
    checkbox selector, not the comma prompt, still runs."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", False),
    ]
    qr_lines = ["Q" * 9 for _ in range(7)]

    for offset in (4, 5, 6, 7):  # former blind heights, all must omit QR
        height = len(qr_lines) + len(devices) + offset
        result, frames = _run_fitted_selection(devices, height,
                                               keys=[ord(' '), 10],
                                               qr_lines=qr_lines)
        frame = "".join("".join(f) for f in frames)
        assert all(line not in frame for line in qr_lines), (offset, frame)
        assert "Found 2 devices:" in frame, offset
        assert result == [0], offset  # key-driven, list-only degradation


def test_curses_selection_fit_exact_real_frame_height_draws_qr():
    """qr+devices+8 rows (no status) is exactly the height real curses
    needs: the qr+devices+7 drawn content rows PLUS the cursor advance
    from the hint's trailing newline (at +7 real ncurses returns ERR —
    verified in a pty: 16/16 rows -> ERR, 15/16 -> OK). The check must
    PASS there — tight against that boundary, not padded."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", False),
    ]
    qr_lines = ["Q" * 9 for _ in range(7)]
    # 17 = 16 content rows + 1 for the hint's cursor advance
    height = len(qr_lines) + len(devices) + 8

    result, frames = _run_fitted_selection(devices, height, keys=[10],
                                           qr_lines=qr_lines)

    frame = "".join(frames[0])
    assert all(line in frame for line in qr_lines), frame
    assert frame.index(qr_lines[0]) < frame.index("Found 2 devices:")
    assert result == []


def test_curses_selection_status_line_counts_a_row_in_fit():
    """The scan-failed status line costs a drawn row AND a required
    spare row: at exactly qr+devices+8 a status event must drop the QR
    (list + status then keep fitting); at qr+devices+9 the QR survives
    alongside the status line — pinning the `+1 if status` term both
    ways against the chrome-8 boundary."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", False),
    ]
    qr_lines = ["Q" * 9 for _ in range(7)]

    def session_with_failure():
        events = queue.Queue()
        events.put({"kind": "scan-failed", "ip": "1.2.3.9"})
        session = MagicMock()
        session.events = events
        return session

    # Exactly 8 chrome rows (7 content + hint newline advance): the
    # status row pushes the check under.
    exact = len(qr_lines) + len(devices) + 8  # 17
    result, frames = _run_fitted_selection(devices, exact, keys=[-1, 10],
                                           qr_lines=qr_lines,
                                           session=session_with_failure())
    first, second = map("".join, frames)
    assert all(line in first for line in qr_lines), first  # pre-status fits
    assert all(line not in second for line in qr_lines), second  # +1 drops QR
    assert "  Pairing failed for 1.2.3.9 — try again" in second
    assert result == []

    # One row more: the QR fits WITH the status line.
    result, frames = _run_fitted_selection(devices, exact + 1, keys=[-1, 10],
                                           qr_lines=qr_lines,
                                           session=session_with_failure())
    first, second = map("".join, frames)
    assert all(line in first for line in qr_lines), first
    assert all(line in second for line in qr_lines), second  # +1 row kept
    assert "  Pairing failed for 1.2.3.9 — try again" in second
    assert result == []


def test_curses_selection_qr_wider_than_terminal_omits_qr_list_only():
    """WIDTH term: a QR line wider than the terminal wraps in ncurses
    and eats rows the height term never counted — the check must fail on
    WIDTH alone (height fits!) so the QR is dropped whole and the list
    runs by itself (list-only degradation, never the comma fallback);
    the keys still drive the selection."""
    devices = [
        ("1.2.3.4", "X1", False),
        ("1.2.3.5", "X2", False),
    ]
    qr_lines = ["Q" * 40 for _ in range(7)]   # 40 columns > 36
    height = len(qr_lines) + len(devices) + 8  # 17 <= 40: height FITS

    result, frames = _run_fitted_selection(devices, height,
                                           keys=[ord(' '), 10],
                                           qr_lines=qr_lines, width=36)

    frame = "".join("".join(f) for f in frames)
    assert all(line not in frame for line in qr_lines), frame
    assert "Found 2 devices:" in frame, frame
    assert result == [0], result  # key-driven selection, no QR needed


def test_curses_selection_wide_terminal_draws_qr():
    """The companion: the SAME QR on a terminal wide enough for every
    line (and tall enough) is drawn — the width term must not
    over-restrict."""
    devices = [
        ("1.2.3.4", "X1", False),
        ("1.2.3.5", "X2", False),
    ]
    qr_lines = ["Q" * 40 for _ in range(7)]
    height = len(qr_lines) + len(devices) + 8

    result, frames = _run_fitted_selection(devices, height, keys=[10],
                                           qr_lines=qr_lines, width=80)

    frame = "".join(frames[0])
    assert all(line in frame for line in qr_lines), frame
    assert frame.index(qr_lines[0]) < frame.index("Found 2 devices:")
    assert result == []


def test_curses_selection_rows_over_width_omit_qr():
    """Device ROWS wrap just like QR lines do — when the row content
    itself exceeds the terminal columns, the QR is never stacked on top
    of a frame whose row count is already wrong (list-only attempt; the
    list itself is all that is left to draw)."""
    devices = [
        ("192.168.100.200", "VeryLongPixelName", False),
        ("192.168.100.201", "VeryLongGalaxyName", False),
    ]
    qr_lines = ["Q" * 20 for _ in range(7)]   # QR line fits 40 columns
    height = len(qr_lines) + len(devices) + 8  # and the height fits too

    result, frames = _run_fitted_selection(devices, height,
                                           keys=[ord(' '), 10],
                                           qr_lines=qr_lines, width=40)

    frame = "".join("".join(f) for f in frames)
    assert all(line not in frame for line in qr_lines), frame
    assert "Found 2 devices:" in frame, frame
    assert result == [0], result


def test_curses_selection_scan_event_appends_and_returns():
    """A 'scanned' event on a timeout tick appends the phone's record to
    the SAME devices list (cli's batch reads it back), force-checks it,
    and returns immediately — the scan acts as confirm."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", False),
    ]
    identity = {"brand": "google", "model": "Pixel 9"}
    events = queue.Queue()
    events.put({"kind": "scanned", "ip": "9.9.9.9", "short": "PIXEL",
                "port": 41234, "target": "9.9.9.9:41234",
                "identity": identity})
    session = MagicMock()
    session.events = events
    timeouts = []

    def fake_wrapper(main_func):
        keys = [-1]  # one timeout tick — no real key pressed

        class FakeStdscr:
            def clear(self): pass
            def addstr(self, s): pass
            def refresh(self): pass
            def timeout(self, ms): timeouts.append(ms)
            def getch(self): return keys.pop(0)

        return main_func(FakeStdscr())

    with patch('curses.wrapper', side_effect=fake_wrapper), \
         patch('curses.curs_set'):
        from adb_wifi_connect.ui import _curses_selection
        result = _curses_selection(devices, session=session)

    assert timeouts == [200]
    assert result == [2]
    assert len(devices) == 3, "same list must grow for cli's batch"
    assert devices[2] == ("9.9.9.9", "PIXEL", True, True, 41234, identity)


def test_curses_selection_scan_failed_shows_status_and_keeps_waiting():
    """A 'scan-failed' event sets the status line, redraws, and keeps
    the selector open for the next scan."""
    devices = [("192.168.1.167", "Pixel", False)]
    events = queue.Queue()
    events.put({"kind": "scan-failed", "ip": "1.2.3.9"})
    session = MagicMock()
    session.events = events
    rows = []

    def fake_wrapper(main_func):
        keys = [-1, 10]  # failed tick → keep waiting; Enter → confirm

        class FakeStdscr:
            def clear(self): pass
            def addstr(self, s): rows.append(s)
            def refresh(self): pass
            def timeout(self, ms): pass
            def getch(self): return keys.pop(0)

        return main_func(FakeStdscr())

    with patch('curses.wrapper', side_effect=fake_wrapper), \
         patch('curses.curs_set'):
        from adb_wifi_connect.ui import _curses_selection
        result = _curses_selection(devices, session=session)

    assert "  Pairing failed for 1.2.3.9 — try again" in "".join(rows)
    assert result == []


def test_curses_selection_checkbox_toggle():
    """Curses selection: space toggles checkbox, enter confirms."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", True),
    ]

    import curses

    def fake_wrapper(main_func):
        keys = [
            ord(' '),           # toggle device 0 → selected
            curses.KEY_DOWN,    # cursor → 1
            ord(' '),           # toggle device 1 → deselect (was pre-selected)
            10,                 # Enter → confirm
        ]

        class FakeStdscr:
            def clear(self): pass
            def addstr(self, s): pass
            def refresh(self): pass
            def getch(self): return keys.pop(0)

        return main_func(FakeStdscr())

    with patch('curses.wrapper', side_effect=fake_wrapper), \
         patch('curses.curs_set'):
        from adb_wifi_connect.connect import _curses_selection
        result = _curses_selection(devices)

    assert result == [0]  # Only Pixel selected


def test_curses_selection_navigate_and_select():
    """Curses selection: navigate down and select multiple devices."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", False),
        ("192.168.1.173", "OnePlus", False),
    ]

    import curses

    def fake_wrapper(main_func):
        keys = [
            curses.KEY_DOWN,    # cursor 0→1
            curses.KEY_DOWN,    # cursor 1→2
            ord(' '),           # toggle device 2
            curses.KEY_UP,      # cursor 2→1
            ord(' '),           # toggle device 1
            10,                 # Enter
        ]

        class FakeStdscr:
            def clear(self): pass
            def addstr(self, s): pass
            def refresh(self): pass
            def getch(self): return keys.pop(0)

        return main_func(FakeStdscr())

    with patch('curses.wrapper', side_effect=fake_wrapper), \
         patch('curses.curs_set'):
        from adb_wifi_connect.connect import _curses_selection
        result = _curses_selection(devices)

    assert result == [1, 2]  # Samsung and OnePlus selected


def test_interactive_fallback_when_curses_import_fails():
    """Falls back to non-interactive when curses import fails."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", False),
    ]

    import builtins
    real_import = builtins.__import__

    def mock_import(name, *args, **kwargs):
        if name == 'curses':
            raise ImportError("No module named 'curses'")
        return real_import(name, *args, **kwargs)

    with patch('builtins.__import__', side_effect=mock_import):
        with patch('builtins.input', return_value='1'):
            from adb_wifi_connect.connect import _run_interactive_selection
            result = _run_interactive_selection(devices)

    assert result == [0]


def test_interactive_fallback_when_curses_error():
    """Falls back to non-interactive when curses.wrapper raises error."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", False),
    ]

    import curses

    def failing_wrapper(main_func):
        raise curses.error("setupterm(): error occurred (1)")

    with patch('curses.wrapper', side_effect=failing_wrapper):
        with patch('builtins.input', return_value='1'):
            from adb_wifi_connect.connect import _run_interactive_selection
            result = _run_interactive_selection(devices)

    assert result == [0]


from adb_wifi_connect.connect import connect_single_device
from adb_wifi_connect.connect import main


def test_connect_single_device_already_connected():
    """Already connected device returns ip:port without reconnecting."""
    with patch('adb_wifi_connect.flow.is_device_connected', return_value=5555):
        result = connect_single_device("192.168.1.167", skip_pair=True, manual=True)
    assert result == "192.168.1.167:5555"


def test_connect_single_device_new_connection():
    """New connection uses port scan and returns ip:port."""
    with patch('adb_wifi_connect.flow.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.flow.discover_connection_port', return_value=(None, None)), \
         patch('adb_wifi_connect.flow.scan_for_adb_port', return_value=37689):
        result = connect_single_device("192.168.1.172", skip_pair=True, manual=True)
    assert result == "192.168.1.172:37689"


def test_connect_single_device_failure():
    """Connection failure returns None."""
    with patch('adb_wifi_connect.flow.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.flow.discover_connection_port', return_value=(None, None)), \
         patch('adb_wifi_connect.flow.scan_for_adb_port', return_value=None), \
         patch('adb_wifi_connect.flow.run_adb_connect', return_value=False):
        result = connect_single_device("192.168.1.173", skip_pair=True, manual=True)
    assert result is None


def test_main_list_flag():
    """--list flag discovers and shows devices, then exits."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", True),
    ]
    with patch('adb_wifi_connect.cli.discover_all_phones', return_value=devices), \
         patch('sys.argv', ['awc', '--list']), \
         patch('builtins.print') as mock_print:
        main()
    assert mock_print.called


def test_main_single_device_backward_compat():
    """Single device found connects directly without selection UI."""
    devices = [("192.168.1.167", "Pixel", True)]
    with patch('adb_wifi_connect.cli.discover_all_phones', return_value=devices), \
         patch('adb_wifi_connect.cli.connect_single_device', return_value="192.168.1.167:5555"), \
         patch('sys.argv', ['awc']):
        result = main()
    assert result == "192.168.1.167:5555"


def test_main_all_flag():
    """--all flag connects to all discovered devices."""
    devices = [
        ("192.168.1.167", "Pixel", False),
        ("192.168.1.172", "Samsung", False),
    ]
    with patch('adb_wifi_connect.cli.discover_all_phones', return_value=devices), \
         patch('adb_wifi_connect.cli.connect_single_device', side_effect=lambda ip, **kw: f"{ip}:5555"), \
         patch('sys.argv', ['awc', '--all']):
        result = main()
    assert len(result) == 2


def test_connect_backward_compat_single_device():
    """connect() with no ip discovers single device and connects directly."""
    devices = [("192.168.1.167", "Pixel", True)]
    with patch('adb_wifi_connect.flow.discover_all_phones', return_value=devices), \
         patch('adb_wifi_connect.flow.connect_single_device', return_value="192.168.1.167:5555"):
        from adb_wifi_connect.connect import connect
        result = connect()
    assert result == "192.168.1.167:5555"


def test_connect_single_device_explicit_port_skips_discovery():
    """Explicit port skips mDNS discovery and port scanning."""
    with patch('adb_wifi_connect.flow.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.flow.discover_connection_port') as mock_discover, \
         patch('adb_wifi_connect.flow.scan_for_adb_port') as mock_scan, \
         patch('adb_wifi_connect.flow.connect_with_retries', return_value=True):
        result = connect_single_device("192.168.1.167", port=37000, skip_pair=True, manual=True)
    assert result == "192.168.1.167:37000"
    mock_discover.assert_not_called()
    mock_scan.assert_not_called()


def test_connect_single_device_explicit_port_failure():
    """Explicit port that fails returns None without scanning."""
    with patch('adb_wifi_connect.flow.is_device_connected', return_value=None), \
         patch('adb_wifi_connect.flow.discover_connection_port') as mock_discover, \
         patch('adb_wifi_connect.flow.scan_for_adb_port') as mock_scan, \
         patch('adb_wifi_connect.flow.connect_with_retries', return_value=False):
        result = connect_single_device("192.168.1.167", port=37000, skip_pair=True, manual=True)
    assert result is None
    mock_discover.assert_not_called()
    mock_scan.assert_not_called()


def test_connect_passes_port_to_connect_single_device():
    """connect() passes port through to connect_single_device."""
    with patch('adb_wifi_connect.flow.connect_single_device', return_value="192.168.1.167:37000") as mock_csd:
        from adb_wifi_connect.connect import connect
        result = connect(ip="192.168.1.167", port=37000, skip_pair=True, manual=True)
    assert result == "192.168.1.167:37000"
    mock_csd.assert_called_once_with("192.168.1.167", port=37000, skip_pair=True,
                                     manual=True, qr_mode="auto", on_connect=None)
