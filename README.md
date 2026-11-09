# adb-wifi-connect

Pair and connect to Android devices via WiFi ADB. Supports QR code pairing (automatic) and 6-digit code pairing (manual). Connect to multiple devices simultaneously, or run it as a daemon that keeps your devices connected.

## Features

- **QR Code pairing** — generate QR in terminal, phone scans, auto-pairs via mDNS
- **Instant QR** — the QR appears immediately at startup (it's pure local generation); device discovery and adb warm-up run behind it — no waiting before scanning
- **Sixel QR** — pixel-sharp QR on terminals that support Sixel graphics, with automatic ASCII fallback
- **Manual pairing** — enter 6-digit code + port from phone
- **Auto IP discovery** — finds phone on network via mDNS (zeroconf)
- **Port scanning** — falls back to parallel port scan if mDNS fails
- **Retry logic** — retries failed connections before giving up
- **Multi-device** — discover and connect to multiple phones at once
- **Interactive selection** — checkbox UI for desired state: checked = connect it, unchecked = `adb disconnect` it
- **Watch mode** — keeps devices connected forever; auto-reconnects when the phone's port rolls
- **Connect hook** — run a command (e.g. `scrcpy`) after every successful connect
- **Direct/remote mode** — `--direct` connects by address with guided prompts: no mDNS, no QR, works over the internet
- **Cross-platform** — Linux, macOS, Windows

## Install

```bash
# Recommended: uv (installs the `awc` command globally)
uv tool install git+https://github.com/<your-user>/adb-wifi-connect

# Or run once without installing
uvx --from git+https://github.com/<your-user>/adb-wifi-connect awc

# pip fallback
pip install git+https://github.com/<your-user>/adb-wifi-connect

# From a local checkout (add .[all] for every optional feature)
pip install ".[all]"
```

This installs two commands — `awc` (short) and `adb-connect` — no shell
aliases needed. If you have an old `awc` alias in `.bashrc`/`.zshrc`,
delete it so it doesn't shadow the real command.

**Optional extras:** `qr` (terminal QR), `mdns` (auto-discovery),
`sixel` (pixel QR rendering), `all` (everything).

**Requirements:** Python 3.7+, `adb` (Android platform-tools) on PATH —
or point at it with `--adb-path`.

## Usage

```bash
# Interactive — discovers devices; a single ready phone connects
# immediately (--wait keeps the Enter prompt instead)
awc

# Connect to all discovered devices
awc --all

# List discovered devices without connecting
awc --list

# Force manual mode (skip QR)
awc --manual

# Skip pairing (already paired)
awc --skip-pair

# Linger at the single-device prompt instead of auto-connecting
# (keeps the QR up to pair another phone)
awc --wait

# Specify IP and port directly (single device)
awc --ip 192.168.1.100 --port 5555

# adb somewhere unusual (e.g. Android Studio's bundled platform-tools)
awc --adb-path /opt/android-sdk/platform-tools/adb

# Direct/remote path: connect by address, no discovery or QR
awc --direct                          # guided: prompts for IP, pairs only if needed
awc --direct 8.8.8.8:5555             # already-paired device, explicit port
awc --direct 8.8.8.8:37000 123456     # quick pair (port = pairing port), then connect
awc --direct 10.0.0.5:37000 123456 --port 41234   # remote: both ports explicit

# Verbose output: every adb command, mDNS event
awc --debug

# QR rendering: plain ASCII (default), auto-detect Sixel, or force Sixel
awc --qr ascii
awc --qr auto
awc --qr sixel
```

Or, inside an environment where the package is installed:
`python -m adb_wifi_connect`.

## Watch mode

Keeps selected devices connected indefinitely — pairs on first sight if
needed, then silently reconnects whenever the phone reappears (Android
rolls the wireless-debug port every time wireless debugging toggles):

```bash
# Watch all discovered/selected devices
awc --watch

# Watch one known device, already paired
awc --watch --ip 192.168.1.100 --skip-pair

# Watch and launch scrcpy on every (re)connect
awc --watch --on-connect scrcpy
```

Ctrl+C stops cleanly.

## Connect hook

`--on-connect CMD` runs a shell command after **every** successful
connect (one-shot and watch mode). The connected `ip:port` is exposed
as `$AWC_TARGET`:

```bash
awc --watch --on-connect 'adb -s $AWC_TARGET logcat'
awc --on-connect 'echo connected to $AWC_TARGET > /tmp/phone'
```

Hook failures are reported but never kill the tool.

## Direct / remote mode

The original manual path, kept as the **secondary** way in — for when
the camera doesn't work, mDNS is broken, or the phone is on another
network (mDNS never crosses the internet):

| Invocation | What happens |
|------------|--------------|
| `awc --direct` | Guided: prompts for IP (prefills `AWC_DEFAULT_IP`), tries to connect, asks for pairing port+code **only if unreachable** |
| `awc --direct 8.8.8.8:5555` | Connect-only: `ip` + connection port |
| `awc --direct 8.8.8.8:37000 123456` | Quick pair: code present ⇒ port is the **pairing** port; then prompts for the connection port (Enter = auto-discover/scan) |
| `awc --direct 10.0.0.5:37000 123456 --port 41234` | Both ports explicit — the full remote/internet flow |
| `awc --watch --direct 8.8.8.8:5555` | Connect, then keep reconnecting |

Note: `--direct` takes its values **last** — put other flags before it.
`--debug` shows every adb command, which is the point when you're
figuring out *why* a connection fails. `--direct` conflicts with `--ip`.

## QR modes

The QR is rendered **immediately when `awc` starts** (no-arg mode) —
credentials are generated locally, so nothing is waited on first;
discovery and the adb warm-up happen behind it. The ASCII QR is
centered on screen (size from the OS terminal API, with an 80×24
fallback; never truncated).

`--qr ascii` (default) always renders the pairing QR as ASCII art —
guaranteed visible in every terminal.

`--qr auto` probes the terminal for Sixel support (DEC device
attributes) and renders the pairing QR as a real pixel image when
possible — easier for phone cameras to scan than ASCII art. Use it
opt-in: the DA probe can only detect *advertised* support, not actual
painting (e.g. Konsole reports Sixel in DA1 yet renders nothing in
some setups), so blank output is possible.

**Fallback is guaranteed:** if the `sixel` extra isn't installed, the
terminal doesn't support Sixel, the probe fails, or rendering errors,
it silently falls back to the ASCII QR. `--qr sixel` forces Sixel (with
an ASCII fallback notice).

Sixel-capable terminals: mlterm, WezTerm, foot, xterm (+sixel),
mintty, iTerm2 3.5+, and others (Konsole advertises Sixel but rendering
has been unreliable).

## Cross-platform

Works on Linux (full), macOS (full), and Windows (numbered selection,
polling QR wait, `--adb-path` for `adb.exe`). Platform-specific details:
[docs/configuration](docs/configuration/).

## Configuration

Everything is optional. The one setting worth knowing: `AWC_DEFAULT_IP` —
a device IP read in exactly two places: it prefills the IP prompt of
`awc --direct`, and the `flow.connect()` Python entry falls back to it
after failed mDNS discovery (unset → failure reported, no guessed
address). The no-arg `awc` CLI does **not** read it when discovery finds
nothing: it waits for a QR scan or exits with failure. Set it via
environment variable or `.env`; see
[docs/configuration](docs/configuration/) for precedence rules, all
flags, and extras.

## Multi-Device Mode

When multiple devices are found on the network:

1. Script discovers all devices via mDNS and reads their names (best
   effort — rows without a readable name still list)
2. Shows interactive selection list (checkboxes, or numbered input when
   curses is unavailable); with a live QR session, the QR is redrawn
   above the list as ASCII art — omitted entirely if the terminal is too
   short or too narrow to fit the whole frame
3. Already-connected devices shown with status
4. Select the desired state and press Enter — checked devices get
   connected, **unchecked devices that are currently connected get
   disconnected** (desired-state checkboxes)

**Scan wins:** if a phone scans the QR while this is on screen, that
phone is appended to the run and pre-checked — the scan acts as confirm,
no selection needed.

```
=== ADB WiFi Connection ===

        (pairing QR — ASCII lines, drawn when the frame fits)

Found 2 devices:

  [x] 1. Samsung Galaxy S24 (SM-S921B) · 192.168.1.100 A1B2C3D4E5F6-xyz123   Connected
  [ ] 2. Google Pixel 7 (Pixel 7) · 192.168.1.101 a1b2c3d4e5f6   Unpaired

  ↑/↓ move · Space toggle · Enter connect · scan the QR to add · Ctrl+C quit
```

## Dependencies

- **Python 3.7+**
- **`adb`** — must be in PATH (or use `--adb-path`) — Android platform-tools
- **`qrcode`** (optional) — for terminal QR code generation
- **`zeroconf`** (optional) — for mDNS auto-discovery of phone IP and ports
- **`sixel` + Pillow** (optional) — for pixel QR rendering (`pip install ".[sixel]"`)

Everything optional degrades gracefully — the tool works with none of them
(manual pairing + numbered prompts only).

## Documentation

- [docs/](docs/) — documentation index
- [Architecture](docs/architecture/) — pipeline, pairing, watch mode, QR fallback, port scan
- [Configuration](docs/configuration/) — flags, `.env`, extras, cross-platform

## How It Works

### QR Mode (recommended)

1. Script generates random pairing credentials and displays a QR code in the terminal
2. On phone: Settings → Developer options → Wireless debugging → Pair device with QR code
3. Phone scans QR → broadcasts mDNS service → script discovers and pairs automatically
4. Connection port discovered via mDNS → `adb connect` runs automatically

### Manual Mode (fallback)

1. On phone: Settings → Developer options → Wireless debugging → Pair device with pairing code
2. Enter the 6-digit code and pairing port shown on phone
3. Script pairs, then auto-discovers connection port via mDNS or port scanning

### Connection resolution order

explicit `--port` → mDNS connect-service → parallel port scan
(30000–48000, coarse then fine) → common ports (5555/5556) → manual
prompt. Every successful connect is verified with a real
`adb shell` round-trip.
