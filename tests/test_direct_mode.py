import sys
from unittest.mock import patch, MagicMock

import pytest


# --- target parsing ---

def test_parse_direct_target_forms():
    from adb_wifi_connect.flow import parse_direct_target
    assert parse_direct_target("1.2.3.4:5555") == ("1.2.3.4", 5555)
    assert parse_direct_target("1.2.3.4") == ("1.2.3.4", None)
    assert parse_direct_target("phone.example.com:41234") == ("phone.example.com", 41234)
    assert parse_direct_target("1.2.3.4:99999") == (None, None)
    assert parse_direct_target("1.2.3.4:port") == (None, None)
    assert parse_direct_target(":5555") == (None, None)


# --- quick pair: creds given => pair first, port is pairing port ---

def test_direct_quick_pair_pairs_before_connecting():
    from adb_wifi_connect.flow import direct_connect
    order = []

    with patch('adb_wifi_connect.flow.is_device_connected',
               return_value=None), \
         patch('adb_wifi_connect.flow.run_adb_pair',
               side_effect=lambda *a, **k: order.append("pair") or True), \
         patch('adb_wifi_connect.flow.connect_with_retries',
               side_effect=lambda *a, **k: order.append("connect") or True), \
         patch('adb_wifi_connect.flow.discover_connection_port') as mock_disc, \
         patch('adb_wifi_connect.flow.scan_for_adb_port') as mock_scan, \
         patch('adb_wifi_connect.flow.run_on_connect') as hook:
        result = direct_connect("1.2.3.4:37000", code="123456",
                                port=41234, on_connect="scrcpy")
    assert result == "1.2.3.4:41234"
    assert order == ["pair", "connect"], order
    mock_disc.assert_not_called()
    mock_scan.assert_not_called()
    hook.assert_called_once_with("scrcpy", "1.2.3.4:41234")


def test_direct_quick_pair_prompts_for_connect_port():
    from adb_wifi_connect.flow import direct_connect
    with patch('adb_wifi_connect.flow.is_device_connected',
               return_value=None), \
         patch('adb_wifi_connect.flow.run_adb_pair',
               return_value=True) as mock_pair, \
         patch('adb_wifi_connect.flow.ask_connection_port',
               return_value=41234) as mock_acp, \
         patch('adb_wifi_connect.flow.connect_with_retries',
               return_value=True) as mock_cr:
        result = direct_connect("1.2.3.4:37000", code="123456")
    assert result == "1.2.3.4:41234"
    mock_pair.assert_called_once_with("1.2.3.4", 37000, "123456")
    assert mock_acp.call_args.kwargs.get('optional') is True
    mock_cr.assert_called_once()
    assert mock_cr.call_args[0][1] == 41234


# --- no creds: connect first, pair only when unreachable ---

def test_direct_connect_only_explicit_port_no_prompts():
    from adb_wifi_connect.flow import direct_connect
    with patch('adb_wifi_connect.flow.is_device_connected',
               return_value=None), \
         patch('adb_wifi_connect.flow.connect_with_retries',
               return_value=True) as mock_cr, \
         patch('adb_wifi_connect.flow.run_adb_pair') as mock_pair, \
         patch('adb_wifi_connect.flow.ask_pairing_code') as mock_ask, \
         patch('adb_wifi_connect.flow.discover_connection_port') as mock_disc:
        result = direct_connect("1.2.3.4:5555")
    assert result == "1.2.3.4:5555"
    mock_cr.assert_called_once_with("1.2.3.4", 5555,
                                    retries=3, delay=1.0)
    mock_pair.assert_not_called()
    mock_ask.assert_not_called()
    mock_disc.assert_not_called()


def test_direct_already_connected_skips_everything():
    from adb_wifi_connect.flow import direct_connect
    with patch('adb_wifi_connect.flow.is_device_connected',
               return_value=7777), \
         patch('adb_wifi_connect.flow.run_adb_pair') as mock_pair, \
         patch('adb_wifi_connect.flow.connect_with_retries') as mock_cr:
        result = direct_connect("1.2.3.4")
    assert result == "1.2.3.4:7777"
    mock_pair.assert_not_called()
    mock_cr.assert_not_called()


def test_direct_guided_falls_back_to_pairing_prompts():
    from adb_wifi_connect.flow import direct_connect
    with patch('adb_wifi_connect.flow.is_device_connected',
               return_value=None), \
         patch('adb_wifi_connect.flow.discover_connection_port',
               return_value=(None, None)), \
         patch('adb_wifi_connect.flow.scan_for_adb_port',
               return_value=None), \
         patch('adb_wifi_connect.flow.ask_pairing_code',
               return_value="123456") as mock_code, \
         patch('adb_wifi_connect.flow.ask_pairing_port',
               return_value=37000) as mock_pport, \
         patch('adb_wifi_connect.flow.run_adb_pair',
               return_value=True) as mock_pair, \
         patch('adb_wifi_connect.flow.ask_connection_port',
               return_value=41234) as mock_cport, \
         patch('adb_wifi_connect.flow.connect_with_retries',
               return_value=True):
        result = direct_connect("1.2.3.4")
    assert result == "1.2.3.4:41234"
    mock_code.assert_called_once_with(allow_empty=True)
    mock_pport.assert_called_once()
    mock_pair.assert_called_once_with("1.2.3.4", 37000, "123456")
    mock_cport.assert_called_once_with(optional=True)


def test_direct_guided_gives_up_without_code():
    from adb_wifi_connect.flow import direct_connect
    with patch('adb_wifi_connect.flow.is_device_connected',
               return_value=None), \
         patch('adb_wifi_connect.flow.discover_connection_port',
               return_value=(None, None)), \
         patch('adb_wifi_connect.flow.scan_for_adb_port',
               return_value=None), \
         patch('adb_wifi_connect.flow.ask_pairing_code',
               return_value=None), \
         patch('adb_wifi_connect.flow.run_adb_pair') as mock_pair:
        result = direct_connect("1.2.3.4")
    assert result is None
    mock_pair.assert_not_called()


def test_direct_invalid_target_fails_before_any_action():
    from adb_wifi_connect.flow import direct_connect
    with patch('adb_wifi_connect.flow.is_device_connected') as mock_chk, \
         patch('adb_wifi_connect.flow.run_adb_pair') as mock_pair:
        result = direct_connect("1.2.3.4:99999")
    assert result is None
    mock_chk.assert_not_called()
    mock_pair.assert_not_called()


# --- prompts ---

def test_ask_ip_uses_env_default_on_empty():
    from adb_wifi_connect.ui import ask_ip
    with patch.dict('os.environ', {'AWC_DEFAULT_IP': '1.2.3.9'}), \
         patch('builtins.input', return_value=''):
        assert ask_ip() == "1.2.3.9"


def test_ask_ip_typed_value_wins():
    from adb_wifi_connect.ui import ask_ip
    with patch.dict('os.environ', {'AWC_DEFAULT_IP': '1.2.3.9'}), \
         patch('builtins.input', return_value='5.5.5.5'):
        assert ask_ip() == "5.5.5.5"


def test_ask_ip_reasks_without_default():
    from adb_wifi_connect.ui import ask_ip
    env = {k: v for k, v in __import__('os').environ.items()
           if k != 'AWC_DEFAULT_IP'}
    with patch.dict('os.environ', env, clear=True), \
         patch('builtins.input', side_effect=['', '7.7.7.7']):
        assert ask_ip() == "7.7.7.7"


def test_ask_connection_port_optional_enter_returns_none():
    from adb_wifi_connect.ui import ask_connection_port
    with patch('builtins.input', return_value=''):
        assert ask_connection_port(optional=True) is None


def test_ask_connection_port_optional_still_validates():
    from adb_wifi_connect.ui import ask_connection_port
    with patch('builtins.input', side_effect=['abc', '41234']):
        assert ask_connection_port(optional=True) == 41234


def test_ask_pairing_code_allow_empty_returns_none():
    from adb_wifi_connect.ui import ask_pairing_code
    with patch('builtins.input', return_value=''):
        assert ask_pairing_code(allow_empty=True) is None


# --- CLI routing ---

def test_cli_direct_routes_without_discovery_or_qr():
    with patch('adb_wifi_connect.cli.direct_connect',
               return_value="1.2.3.4:5555") as mock_direct, \
         patch('adb_wifi_connect.cli.discover_all_phones') as mock_disc, \
         patch('adb_wifi_connect.cli.start_pairing_session') as mock_qr, \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('sys.argv', ['awc', '--direct', '1.2.3.4:5555']):
        from adb_wifi_connect.cli import main
        result = main()
    assert result == "1.2.3.4:5555"
    mock_disc.assert_not_called()
    mock_qr.assert_not_called()
    assert mock_direct.call_args.kwargs.get('target') == "1.2.3.4:5555"


def test_cli_direct_passes_code():
    with patch('adb_wifi_connect.cli.direct_connect',
               return_value="1.2.3.4:41234") as mock_direct, \
         patch('adb_wifi_connect.cli.discover_all_phones'), \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('sys.argv', ['awc', '--direct', '1.2.3.4:37000', '123456']):
        from adb_wifi_connect.cli import main
        main()
    assert mock_direct.call_args.kwargs.get('target') == "1.2.3.4:37000"
    assert mock_direct.call_args.kwargs.get('code') == "123456"


def test_cli_direct_combines_with_port_flag():
    with patch('adb_wifi_connect.cli.direct_connect',
               return_value="1.2.3.4:41234") as mock_direct, \
         patch('adb_wifi_connect.cli.discover_all_phones'), \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('sys.argv', ['awc', '--direct', '1.2.3.4',
                            '--port', '41234']):
        from adb_wifi_connect.cli import main
        main()
    assert mock_direct.call_args.kwargs.get('port') == 41234


def test_cli_direct_bare_prompts_target():
    with patch('adb_wifi_connect.cli.direct_connect',
               return_value="1.2.3.9:5555") as mock_direct, \
         patch('adb_wifi_connect.cli.discover_all_phones'), \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('sys.argv', ['awc', '--direct']):
        from adb_wifi_connect.cli import main
        main()
    assert mock_direct.call_args.kwargs.get('target') is None


def test_cli_direct_conflicts_with_ip():
    with patch('sys.argv', ['awc', '--direct', '--ip', '1.2.3.4']):
        from adb_wifi_connect.cli import main
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 2


def test_cli_direct_with_watch_connects_then_watches():
    with patch('adb_wifi_connect.cli.direct_connect',
               return_value="1.2.3.4:5555") as mock_direct, \
         patch('adb_wifi_connect.cli.watch_loop') as mock_watch, \
         patch('adb_wifi_connect.cli.discover_all_phones'), \
         patch('adb_wifi_connect.adb.warm_adb'), \
         patch('sys.argv', ['awc', '--watch', '--direct', '1.2.3.4:5555']):
        from adb_wifi_connect.cli import main
        main()
    mock_direct.assert_called_once()
    mock_watch.assert_called_once()
    assert mock_watch.call_args[0][0] == ["1.2.3.4"]


def test_cli_help_documents_direct_forms(capsys):
    with patch('sys.argv', ['awc', '--help']):
        from adb_wifi_connect.cli import main
        with pytest.raises(SystemExit) as exc:
            main()
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--direct" in out
    assert "IP[:PORT] [CODE]" in out
    assert "37000 123456" in out          # quick-pair example
    assert "--watch --direct" in out      # composition example
