"""
ADB WiFi Connection Script
Pairs and connects to Android device via WiFi ADB.
Supports QR code pairing (automatic) and 6-digit code pairing (manual).
"""

from .adb import (is_device_connected, run_adb_pair, run_adb_connect,
                  connect_with_retries, scan_for_adb_port, run_on_connect,
                  subprocess)
from .cli import main
from .env import load_env, _parse_env_file
from .flow import connect, connect_single_device
from .mdns import (MDNS_TYPE, MDNS_CONNECT_TYPE, discover_pairing_service,
                   discover_connection_port, discover_all_phones, _resolve_host,
                   Zeroconf, ServiceBrowser)
from .qr import (generate_code, generate_device_name, format_qr_data,
                 display_qr, _display_qr_ascii, _query_device_attrs,
                 supports_sixel, _render_sixel_qr, QR_FORMAT, qrcode)
from .ui import (ask_pairing_code, ask_pairing_port, ask_connection_port,
                 select_devices, _run_interactive_selection, _curses_selection,
                 _run_noninteractive_selection, wait_for_pairing)
from .watch import (WatchListener, watch_cycle, watch_loop,
                    _start_watch_browsers, _watch_rescan)


if __name__ == "__main__":
    main()
