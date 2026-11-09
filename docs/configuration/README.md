# Configuration

All ways to configure adb-wifi-connect.

## Configuration precedence

```
real environment variable  >  ./.env  >  ~/.config/awc/.env
```

The tool reads `.env` files at startup (`load_env`), but never overrides
variables already present in the environment. Only `AWC_*` keys are
imported from files.

## Environment variables

| Variable | Required | Default | Meaning |
|----------|----------|---------|---------|
| `AWC_DEFAULT_IP` | no | *(none)* | Read in two places: prefills the `--direct` IP prompt, and the `flow.connect()` Python entry falls back to it after failed mDNS discovery (unset → failure reported instead of a guessed address). The no-arg `awc` CLI does **not** read it when discovery finds nothing — it waits for a QR scan or exits with failure. |

`.env` example (tracked as `.env.example`; your real `.env` is gitignored):

```dotenv
# Prefills the --direct IP prompt; flow.connect() (Python API) falls back
# to it after failed discovery. The no-arg awc CLI does not read it when
# discovery finds nothing (QR wait or failure).
AWC_DEFAULT_IP=192.168.1.100
```

```bash
# Equivalent, straight from the shell:
export AWC_DEFAULT_IP=192.168.1.100
```

## CLI flags

| Flag | Description |
|------|-------------|
| `--ip IP` | Device IP — skips discovery (single-device mode) |
| `--port N` | Connection port — skips mDNS port discovery and scanning |
| `--skip-pair` | Skip pairing (phone already paired) |
| `--manual` | Force manual 6-digit pairing (no QR) |
| `--wait` | Keep the single-device Enter prompt (linger with the QR to pair another phone) instead of connecting a ready device immediately |
| `--all` | Connect to every discovered device without asking |
| `--list` | List discovered devices and exit |
| `--direct [IP[:PORT] [CODE]]` | Manual/remote path: no discovery, no QR. Connect-first by address; pairs only when unreachable (guided) or immediately when a code is given. `--direct` values come **last**; conflicts with `--ip`; composes with `--port` (connection port), `--watch`, `--debug` |
| `--watch` | Reconnect forever until Ctrl+C (pairs when needed) |
| `--on-connect CMD` | Shell command run after each successful connect; device in `$AWC_TARGET` |
| `--qr auto\|sixel\|ascii` | QR rendering mode (default `ascii`; `auto` probes for Sixel — DA1 can advertise Sixel without rendering). Inside the multi-device selector the QR is always redrawn as ASCII art (a Sixel image can't be repainted in curses) |
| `--adb-path PATH` | Path to the adb executable (default: `adb` on PATH) |
| `--debug` | DEBUG logging: every adb command, mDNS event |

Selection semantics: the list reflects **desired state** — checked
devices get connected, checked-already stay, unchecked-but-connected
devices are disconnected (by exact serial — `Disconnected:` lines only
for serials adb really removed). Status column priority:
**Connected → Paired → Unpaired → Not connected** (paired = the phone
advertises the `_adb-tls-connect` service).

Exit codes: `0` on success, `1` on failure (pairing/connect failures,
no devices found). Output never contains stray return-value dumps.

Examples:

```bash
awc --watch --on-connect scrcpy          # keep connected, launch scrcpy
awc --qr auto                           # pixel QR when terminal supports Sixel
awc --adb-path "/opt/android-sdk/platform-tools/adb"
```

## Optional extras

| Extra | Installs | Enables |
|-------|----------|---------|
| `qr` | `qrcode` | terminal QR generation |
| `mdns` | `zeroconf` | automatic device/port discovery |
| `sixel` | `sixel`, `qrcode[pil]` | pixel-perfect Sixel QR |
| `all` | everything | full feature set |

With none installed the tool still works: manual pairing, numbered
selection, ASCII/`QR data` output.

```bash
uv tool install --editable ".[all]"
```

## Cross-platform notes

| Platform | Status | Notes |
|----------|--------|-------|
| Linux | full | curses checkbox UI, Enter-to-manual during QR wait, Sixel where the terminal supports it |
| macOS | full | Same as Linux; iTerm2 3.5+ renders Sixel |
| Windows | works | Numbered selection instead of curses; QR wait polls stdin readiness (`msvcrt.kbhit` — `select()` cannot watch stdin there); point `--adb-path` at `adb.exe` if it is not on PATH |

`adb` must be on PATH (or given via `--adb-path`) on every platform.
