"""Identity read (adb.read_identity) + label composition (ui.format_label).

Composition rules are spec-pinned (task-4-brief): hyphens in model are
truth (SM-G9960, never SM_G9960), the separator is the middle dot, and
the status column sits after three spaces in both row printers.
"""
from unittest.mock import MagicMock, patch

from adb_wifi_connect.adb import read_identity
from adb_wifi_connect.flow import enrich_devices
from adb_wifi_connect.ui import _print_device_list, format_label

REC = lambda ident, ip='192.168.1.19', s='A1B2C3D4E5F6-xyz123': (ip, s, False, True, 41234, ident)


# --- composition matrix ---

def test_label_marketname_with_brand():
    ident = {'brand': 'samsung', 'marketname': 'Galaxy S21+',
             'model': 'SM-G9960', 'device_name': ''}
    assert format_label(REC(ident)) == (
        'Samsung Galaxy S21+ (SM-G9960) · '
        '192.168.1.19 A1B2C3D4E5F6-xyz123')


def test_label_device_name_fallback():   # verified live: marketname ''
    ident = {'brand': 'samsung', 'marketname': '',
             'device_name': 'S21', 'model': 'SM-G9960'}
    assert format_label(REC(ident)) == (
        'Samsung S21 (SM-G9960) · 192.168.1.19 A1B2C3D4E5F6-xyz123')


def test_label_brand_model_fallback():   # no marketing anywhere
    ident = {'brand': 'Google', 'marketname': '', 'device_name': '',
             'model': 'Pixel 7'}
    # name == brand+model, parens still carry model (rule 17)
    assert format_label(REC(ident)) == (
        'Google Pixel 7 (Pixel 7) · 192.168.1.19 A1B2C3D4E5F6-xyz123')


def test_label_no_identity():
    assert format_label(REC(None)) == '192.168.1.19 A1B2C3D4E5F6-xyz123'


def test_label_marketname_already_branded():
    # marketing starts with the brand -> no double brand prefix
    ident = {'brand': 'samsung', 'marketname': 'Samsung Galaxy S21',
             'model': 'SM-G9960', 'device_name': ''}
    assert format_label(REC(ident)) == (
        'Samsung Galaxy S21 (SM-G9960) · '
        '192.168.1.19 A1B2C3D4E5F6-xyz123')


def test_label_identity_without_model():
    # identity present, model blank -> no parens (rule 18)
    ident = {'brand': 'samsung', 'marketname': '', 'model': '',
             'device_name': 'S21'}
    assert format_label(REC(ident)) == (
        'Samsung S21 · 192.168.1.19 A1B2C3D4E5F6-xyz123')
    # ...and when name is empty too -> bare ip short_name
    blank = {'brand': '', 'marketname': '', 'model': '', 'device_name': ''}
    assert format_label(REC(blank)) == '192.168.1.19 A1B2C3D4E5F6-xyz123'


def test_label_short_tuple_without_identity_slot():
    # legacy 3-tuple record: no identity slot at all
    rec = ('192.168.1.19', 'A1B2C3D4E5F6-xyz123', False)
    assert format_label(rec) == '192.168.1.19 A1B2C3D4E5F6-xyz123'


def test_format_label_never_raises_on_malformed_records():
    assert isinstance(format_label(None), str)
    assert isinstance(format_label(('1.2.3.4',)), str)
    # truthy non-dict identity degrades to the bare label
    assert format_label(('1.2.3.4', 'X', False, False, None,
                         'not-a-dict')) == '1.2.3.4 X'


# --- row printers wired to format_label ---

def test_print_device_list_row_uses_format_label(capsys):
    ident = {'brand': 'samsung', 'marketname': '', 'model': 'SM-G9960',
             'device_name': 'S21'}
    _print_device_list([REC(ident)])
    out = capsys.readouterr().out
    assert ('  1. Samsung S21 (SM-G9960) · 192.168.1.19 A1B2C3D4E5F6-xyz123'
            '   Paired') in out, out


def test_curses_row_uses_format_label():
    ident = {'brand': 'samsung', 'marketname': '', 'model': 'SM-G9960',
             'device_name': 'S21'}
    devices = [REC(ident)]

    import curses

    rows = []

    def fake_wrapper(main_func):
        class FakeStdscr:
            def clear(self): pass
            def addstr(self, s): rows.append(s)
            def refresh(self): pass
            def getch(self): return 10  # Enter -> confirm immediately

        return main_func(FakeStdscr())

    with patch('curses.wrapper', side_effect=fake_wrapper), \
         patch('curses.curs_set'):
        from adb_wifi_connect.ui import _curses_selection
        _curses_selection(devices)

    expected = ('> [ ] 1. Samsung S21 (SM-G9960) · '
                '192.168.1.19 A1B2C3D4E5F6-xyz123   Paired\n')
    assert expected in rows, rows


# --- adb.read_identity ---

def test_read_identity_parses_four_lines_and_handles_failure(capsys):
    mock = MagicMock(stdout='Galaxy S21+\nsamsung\nSM-G9960\nS21\n',
                     returncode=0)
    with patch('adb_wifi_connect.adb.subprocess.run',
               return_value=mock):
        ident = read_identity('192.168.1.19:41234')
    assert ident == {"marketname": "Galaxy S21+", "brand": "samsung",
                     "model": "SM-G9960", "device_name": "S21"}
    assert capsys.readouterr().out == ""  # echo=False: quiet

    with patch('adb_wifi_connect.adb.subprocess.run',
               side_effect=OSError('adb missing')):
        assert read_identity('192.168.1.19:41234') is None

    failing = MagicMock(stdout='adb error', returncode=1)
    with patch('adb_wifi_connect.adb.subprocess.run',
               return_value=failing):
        assert read_identity('192.168.1.19:41234') is None


def test_read_identity_argv_quiet_three_second_timeout():
    mock = MagicMock(stdout='\nsamsung\nSM-G9960\n\n', returncode=0)
    with patch('adb_wifi_connect.adb.subprocess.run',
               return_value=mock) as m:
        read_identity('192.168.1.19:41234')
    assert m.call_args[0][0] == [
        'adb', '-s', '192.168.1.19:41234', 'shell',
        'getprop ro.product.marketname; getprop ro.product.brand; '
        'getprop ro.product.model; settings get global device_name']
    assert m.call_args[1]['timeout'] == 3
    assert 'shell' not in m.call_args[1]  # argv list, never shell=True


def test_read_identity_blank_lines_become_empty_strings():
    mock = MagicMock(stdout='\nsamsung\n\n\n', returncode=0)
    with patch('adb_wifi_connect.adb.subprocess.run', return_value=mock):
        assert read_identity('192.168.1.19:41234') == {
            "marketname": "", "brand": "samsung",
            "model": "", "device_name": ""}


def test_read_identity_settings_null_sentinel_becomes_empty():
    """`settings get global device_name` prints the literal `null` when
    the key is unset — Android's sentinel, not a name. Keep it raw and
    format_label renders `Samsung null (SM-G9960)`; normalize it to ""
    (the settings line ONLY — getprop lines already handle blank) so the
    label falls back to brand+model."""
    mock = MagicMock(stdout='\nsamsung\nSM-G9960\nnull\n', returncode=0)
    with patch('adb_wifi_connect.adb.subprocess.run', return_value=mock):
        ident = read_identity('192.168.1.19:41234')
    assert ident == {"marketname": "", "brand": "samsung",
                     "model": "SM-G9960", "device_name": ""}
    label = format_label(REC(ident))
    assert "null" not in label.lower(), label
    assert label == (
        'Samsung SM-G9960 (SM-G9960) · '
        '192.168.1.19 A1B2C3D4E5F6-xyz123')


# --- flow.enrich_devices: orchestrator ---

PAIRED = ('192.168.1.19', 'A1B2C3D4E5F6-xyz123', False, True, 41234, None)
UNPAIRED = ('192.168.1.20', 'Pixel-abc', False, False, None, None)
BANNER = '  Reading device names…'


def test_enrich_paired_device_connects_then_reads_identity(capsys):
    ident = {"brand": "samsung", "marketname": "Galaxy S21+",
             "model": "SM-G9960", "device_name": "S21"}
    devices = [PAIRED]
    with patch('adb_wifi_connect.adb.connected_serials',
               return_value=set()), \
         patch('adb_wifi_connect.flow.run_adb_connect',
               return_value=True) as mock_conn, \
         patch('adb_wifi_connect.adb.read_identity',
               return_value=ident) as mock_read:
        result = enrich_devices(devices)
    # connect-for-naming: quiet (echo=False), hard 3s cap, then the
    # read happens over the resulting ip:port serial
    mock_conn.assert_called_once_with('192.168.1.19', 41234,
                                      timeout=3, echo=False)
    mock_read.assert_called_once_with('192.168.1.19:41234')
    assert result[0][5] == ident
    # is_connected / is_paired are discovery snapshots: enrichment
    # connected the phone for naming but must not rewrite them
    assert result[0][:4] == PAIRED[:4], result[0]
    assert devices[0] is PAIRED  # input records never mutated
    assert BANNER in capsys.readouterr().out


def test_enrich_unpaired_device_never_connects():
    with patch('adb_wifi_connect.adb.connected_serials') as mock_live, \
         patch('adb_wifi_connect.flow.run_adb_connect') as mock_conn, \
         patch('adb_wifi_connect.adb.read_identity') as mock_read:
        result = enrich_devices([UNPAIRED])
    # no candidate -> no adb traffic at all, not even an `adb devices`
    mock_live.assert_not_called()
    mock_conn.assert_not_called()
    mock_read.assert_not_called()
    assert result[0][5] is None
    assert result[0][:4] == UNPAIRED[:4]


def test_enrich_devices_read_failure_falls_back():
    with patch('adb_wifi_connect.adb.connected_serials',
               return_value=set()), \
         patch('adb_wifi_connect.flow.run_adb_connect',
               return_value=True), \
         patch('adb_wifi_connect.adb.read_identity',
               side_effect=OSError('adb exploded')):
        result = enrich_devices([PAIRED])  # must not raise
    assert result[0][5] is None
    assert result[0][:4] == PAIRED[:4]


def test_enrich_prints_banner_only_with_candidates(capsys):
    with patch('adb_wifi_connect.adb.connected_serials'), \
         patch('adb_wifi_connect.flow.run_adb_connect',
               return_value=False), \
         patch('adb_wifi_connect.adb.read_identity', return_value=None):
        enrich_devices([UNPAIRED,
                        ('192.168.1.21', 'Other-x', False, False,
                         None, None)])
        assert BANNER not in capsys.readouterr().out

        enrich_devices([UNPAIRED, PAIRED])
        out = capsys.readouterr().out
    assert BANNER in out, out
    assert out.count(BANNER) == 1, out  # one banner, never per device


def test_enrich_reuses_live_serial_without_connecting():
    mdns = 'adb-A1B2C3D4E5F6-xyz123._adb-tls-connect._tcp'
    # only the mDNS-form serial is live -> read over it, no connect
    with patch('adb_wifi_connect.adb.connected_serials',
               return_value={mdns}), \
         patch('adb_wifi_connect.flow.run_adb_connect') as mock_conn, \
         patch('adb_wifi_connect.adb.read_identity',
               return_value={'brand': 'Google', 'marketname': '',
                             'model': 'Pixel 7',
                             'device_name': ''}) as mock_read:
        result = enrich_devices([PAIRED])
    mock_conn.assert_not_called()  # already live: never a 2nd transport
    mock_read.assert_called_once_with(mdns)
    assert result[0][5]['brand'] == 'Google'

    # both forms live -> prefer the ip:port serial
    with patch('adb_wifi_connect.adb.connected_serials',
               return_value={'192.168.1.19:41234', mdns}), \
         patch('adb_wifi_connect.flow.run_adb_connect') as mock_conn, \
         patch('adb_wifi_connect.adb.read_identity') as mock_read:
        enrich_devices([PAIRED])
    mock_conn.assert_not_called()
    mock_read.assert_called_once_with('192.168.1.19:41234')


def test_enrich_never_disconnects_opened_transports():
    with patch('adb_wifi_connect.adb.connected_serials',
               return_value=set()), \
         patch('adb_wifi_connect.flow.run_adb_connect',
               return_value=True), \
         patch('adb_wifi_connect.adb.read_identity',
               return_value=None), \
         patch('adb_wifi_connect.adb.run_adb_disconnect') as mock_disc:
        enrich_devices([PAIRED])
    mock_disc.assert_not_called()  # transports it opens stay connected


def test_cli_calls_enrich_right_after_discovery():
    """cli.main enriches immediately after discovery and feeds the
    enriched list on to routing (single-device path here)."""
    order = []
    devices = [('1.2.3.4', 'A', False, False, None, None)]

    def fake_discover(**kw):
        order.append('discovery')
        return devices

    def fake_enrich(recs):
        order.append('enrich')
        return recs

    with patch('adb_wifi_connect.cli.discover_all_phones',
               side_effect=fake_discover), \
         patch('adb_wifi_connect.cli.enrich_devices',
               side_effect=fake_enrich) as mock_enrich, \
         patch('adb_wifi_connect.cli.connect_single_device',
               return_value='1.2.3.4:5555'), \
         patch('sys.argv', ['awc']):
        from adb_wifi_connect.cli import main
        main()
    assert order == ['discovery', 'enrich'], order
    mock_enrich.assert_called_once_with(devices)
