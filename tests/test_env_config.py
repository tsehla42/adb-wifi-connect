import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

import adb_wifi_connect.connect
connect = sys.modules["adb_wifi_connect.connect"]
from adb_wifi_connect.connect import load_env, _parse_env_file


def _write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


# --- parser ---

def test_parse_env_file_basic(tmp_path):
    f = _write(tmp_path / ".env", "AWC_DEFAULT_IP=192.168.1.100\n")
    assert _parse_env_file(f) == {"AWC_DEFAULT_IP": "192.168.1.100"}


def test_parse_env_file_comments_blanks_quotes(tmp_path):
    f = _write(tmp_path / ".env",
               "# comment\n"
               "\n"
               "   \n"
               "AWC_DEFAULT_IP = 192.168.1.100 \n"
               "AWC_QUOTED='10.0.0.1'\n"
               "AWC_DQ=\"10.0.0.2\"\n"
               "NOT_A_KEY_LINE\n"
               "=novalue\n")
    assert _parse_env_file(f) == {
        "AWC_DEFAULT_IP": "192.168.1.100",
        "AWC_QUOTED": "10.0.0.1",
        "AWC_DQ": "10.0.0.2",
    }


def test_parse_env_file_missing_returns_empty(tmp_path):
    assert _parse_env_file(tmp_path / "nope.env") == {}


# --- load_env precedence ---

def test_load_env_real_env_wins_over_files(tmp_path):
    cwd = tmp_path / "cwd"
    home = tmp_path / "home"
    _write(cwd / ".env", "AWC_DEFAULT_IP=192.168.1.100\n")
    _write(home / ".config" / "awc" / ".env", "AWC_DEFAULT_IP=192.168.1.101\n")
    environ = {"AWC_DEFAULT_IP": "10.0.0.1"}
    load_env(environ=environ, cwd=cwd, home=home)
    assert environ["AWC_DEFAULT_IP"] == "10.0.0.1"


def test_load_env_cwd_beats_config_dir(tmp_path):
    cwd = tmp_path / "cwd"
    home = tmp_path / "home"
    _write(cwd / ".env", "AWC_DEFAULT_IP=192.168.1.100\n")
    _write(home / ".config" / "awc" / ".env", "AWC_DEFAULT_IP=192.168.1.101\n")
    environ = {}
    load_env(environ=environ, cwd=cwd, home=home)
    assert environ["AWC_DEFAULT_IP"] == "192.168.1.100"


def test_load_env_config_dir_fallback_when_no_cwd_file(tmp_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    home = tmp_path / "home"
    _write(home / ".config" / "awc" / ".env", "AWC_DEFAULT_IP=192.168.1.101\n")
    environ = {}
    load_env(environ=environ, cwd=cwd, home=home)
    assert environ["AWC_DEFAULT_IP"] == "192.168.1.101"


def test_load_env_missing_files_is_silent(tmp_path):
    environ = {}
    load_env(environ=environ, cwd=tmp_path, home=tmp_path / "nohome")
    assert environ == {}


def test_load_env_only_applies_awc_prefixed_keys(tmp_path):
    cwd = tmp_path / "cwd"
    _write(cwd / ".env",
           "AWC_DEFAULT_IP=192.168.1.100\n"
           "SECRET_TOKEN=abc\n")
    environ = {}
    load_env(environ=environ, cwd=cwd, home=tmp_path / "home")
    assert environ == {"AWC_DEFAULT_IP": "192.168.1.100"}


def test_load_env_never_touches_real_os_environ(tmp_path):
    """load_env must mutate only the environ dict it is given."""
    cwd = tmp_path / "cwd"
    _write(cwd / ".env", "AWC_DEFAULT_IP=192.168.1.100\n")
    before = dict(os.environ)
    load_env(environ={}, cwd=cwd, home=tmp_path / "home")
    assert dict(os.environ) == before


# --- main() wires the loader ---

def test_main_calls_load_env():
    with patch('adb_wifi_connect.cli.load_env') as mock_load, \
         patch('adb_wifi_connect.cli.discover_all_phones',
               return_value=[]), \
         patch('sys.argv', ['awc', '--list']):
        from adb_wifi_connect.connect import main
        with pytest.raises(SystemExit):
            main()
    mock_load.assert_called_once()


# --- default-IP behavior in the connect flow ---

def test_connect_uses_awc_default_ip_from_env():
    devices = []
    with patch('adb_wifi_connect.flow.discover_all_phones',
               return_value=devices), \
         patch('adb_wifi_connect.flow.connect_single_device',
               return_value="192.168.1.100:5555") as csd, \
         patch.dict(os.environ, {"AWC_DEFAULT_IP": "192.168.1.100"}):
        from adb_wifi_connect.connect import connect
        result = connect(skip_pair=True, manual=True)
    assert result == "192.168.1.100:5555"
    assert csd.call_args[0][0] == "192.168.1.100"


def test_connect_without_default_ip_gives_up_cleanly():
    env = {k: v for k, v in os.environ.items() if k != "AWC_DEFAULT_IP"}
    with patch('adb_wifi_connect.flow.discover_all_phones',
               return_value=[]), \
         patch('adb_wifi_connect.flow.connect_single_device') as csd, \
         patch.dict(os.environ, env, clear=True):
        from adb_wifi_connect.connect import connect
        result = connect(skip_pair=True, manual=True)
    assert result is None
    csd.assert_not_called()


def test_no_hardcoded_ip_default_left():
    """IP_DEFAULT constant must be gone — config comes from env only."""
    assert not hasattr(connect, "IP_DEFAULT"), "IP_DEFAULT still defined"
