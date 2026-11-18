"""awc -l: discovery merges adb-connected phones mDNS missed, records carry
their live adb serials (slot 6), rows end in the serial, -v adds detail."""
from unittest.mock import MagicMock, patch

S = "adb-ABC-x1._adb-tls-connect._tcp"


# --- merge_connected (pure) ---

def test_merge_adds_missed_phone():
    from adb_wifi_connect.mdns import merge_connected
    records = [("10.0.0.5", "OP-1", True, True, 41000, None)]
    serials = {"10.0.0.5:41000", "10.0.0.9:46000"}
    services = [("OP-1", "10.0.0.5", 41000), ("ABC-x1", "10.0.0.9", 46000)]
    result = merge_connected(records, serials, services)
    assert len(result) == 2
    assert result[0][6] == ("10.0.0.5:41000",)
    assert result[1] == ("10.0.0.9", "ABC-x1", True, True, 46000, None,
                         ("10.0.0.9:46000",))


def test_merge_collapses_duplicate_serials():
    from adb_wifi_connect.mdns import merge_connected
    result = merge_connected([], {"10.0.0.9:46000", S},
                             [("ABC-x1", "10.0.0.9", 46000)])
    assert result == [("10.0.0.9", "ABC-x1", True, True, 46000, None,
                       ("10.0.0.9:46000", S))]


def test_merge_ignores_non_network_serials():
    from adb_wifi_connect.mdns import merge_connected
    assert merge_connected([], {"RFCNC0WP85Z", "emulator-5554"}, []) == []


def test_merge_mdns_services_failure():
    from adb_wifi_connect.mdns import merge_connected
    records = [("10.0.0.5", "OP-1", False, True, 41000, None)]
    result = merge_connected(records, {"10.0.0.5:41000", S}, [])
    assert len(result) == 1
    assert result[0][2] is True
    assert result[0][6] == ("10.0.0.5:41000",)


# --- adb.mdns_services ---

def test_mdns_services_parses_connect_rows():
    from adb_wifi_connect.adb import mdns_services
    out = ("List of discovered mdns services\n"
           "adb-ABC-x1\t_adb-tls-connect._tcp\t10.0.0.9:46000\n"
           "adb-P-2\t_adb-tls-pairing._tcp\t10.0.0.9:37000\n")
    with patch('adb_wifi_connect.adb.subprocess.run',
               return_value=MagicMock(stdout=out, returncode=0)):
        assert mdns_services() == [("ABC-x1", "10.0.0.9", 46000)]
    with patch('adb_wifi_connect.adb.subprocess.run',
               side_effect=OSError("no adb")):
        assert mdns_services() == []


# --- discover_all_phones wiring ---

def test_merge_runs_without_zeroconf():
    with patch('adb_wifi_connect.mdns.Zeroconf', None), \
         patch('adb_wifi_connect.mdns.connected_serials',
               return_value={"10.0.0.9:46000"}), \
         patch('adb_wifi_connect.mdns.mdns_services',
               return_value=[("ABC-x1", "10.0.0.9", 46000)]):
        from adb_wifi_connect.mdns import discover_all_phones
        result = discover_all_phones(timeout=1)
    assert result == [("10.0.0.9", "ABC-x1", True, True, 46000, None,
                       ("10.0.0.9:46000",))]


def test_merge_record_keeps_own_mdns_serial_without_services():
    from adb_wifi_connect.mdns import merge_connected
    records = [("10.0.0.9", "ABC-x1", True, True, 46000, None)]
    result = merge_connected(records, {"10.0.0.9:46000", S}, [])
    assert result == [("10.0.0.9", "ABC-x1", True, True, 46000, None,
                       ("10.0.0.9:46000", S))]


# --- -l rows / -v detail ---

REC7 = ("10.0.0.9", "ABC", True, True, 46000, None,
        ("10.0.0.9:46000", "adb-ABC._adb-tls-connect._tcp"))


def test_list_verbose_detail(capsys):
    from adb_wifi_connect.ui import _print_device_list
    _print_device_list([REC7], verbose=True)
    out = capsys.readouterr().out
    assert "Found 1 device:" in out, out
    assert ("     serials: 10.0.0.9:46000, adb-ABC._adb-tls-connect._tcp"
            in out), out
    assert "     mdns:    ABC" in out, out
    assert "     port:    46000" in out, out
    _print_device_list([REC7])
    assert "serials:" not in capsys.readouterr().out


def test_cli_lv_flag(capsys):
    with patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[REC7]), \
         patch('adb_wifi_connect.cli.enrich_devices',
               side_effect=lambda recs: recs), \
         patch('sys.argv', ['awc', '-lv']):
        from adb_wifi_connect.cli import main
        main()
    assert "     serials: 10.0.0.9:46000" in capsys.readouterr().out


def test_merge_stale_adb_mdns_view_keeps_one_row():
    # zeroconf sees phone ABC-x1 at .5; adb's stale mdns view says .9 —
    # its mDNS serial belongs to the discovered record, never a 2nd row
    from adb_wifi_connect.mdns import merge_connected
    records = [("10.0.0.5", "ABC-x1", True, True, 41000, None)]
    result = merge_connected(records, {"10.0.0.5:41000", S},
                             [("ABC-x1", "10.0.0.9", 46000)])
    assert result == [("10.0.0.5", "ABC-x1", True, True, 41000, None,
                       ("10.0.0.5:41000", S))]
