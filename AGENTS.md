# adb-wifi-connect

Entry point for AI agents. CLI tool that pairs and connects Android devices over WiFi ADB (QR code or manual pairing), with mDNS discovery, multi-device selection, and a watch daemon.

## Project Structure

```
src/
└── adb_wifi_connect/     # importable package (src layout — never rename to hyphens)
    ├── env.py            # .env loader
    ├── qr.py             # QR credentials + Sixel/ASCII rendering
    ├── adb.py            # subprocess wrappers, ADB_PATH, port scan
    ├── mdns.py           # zeroconf discovery, _browse
    ├── ui.py             # prompts, selection UI, wait_for_pairing
    ├── flow.py           # connect pipeline
    ├── watch.py          # watch daemon
    ├── cli.py            # argparse + main
    ├── connect.py        # re-export facade (import compat)
    ├── __init__.py       # Exports connect(), main(); __version__
    └── __main__.py       # python -m adb_wifi_connect -> connect.main()
tests/
├── conftest.py           # Session-wide guard: escaped PairingSession listener ticks are silent — no test reaches the network
├── test_connect_adb.py   # Unit tests: QR/pair/connect helpers, prompts, adb wrappers
├── test_multi_device.py  # Unit tests: mDNS discovery, selection UI, multi-device CLI flow
├── test_enhancements.py  # Unit tests: argv adb-path, QR modes/Sixel, hooks, watch mode, config
├── test_env_config.py    # Unit tests: .env loader precedence, AWC_DEFAULT_IP fallback
├── test_cli_polish.py    # Unit tests: output rules, run() exit semantics, centering, status column
├── test_instant_qr.py    # Unit tests: PairingSession ordering, scan-wins, warm-up
├── test_direct_mode.py   # Unit tests: --direct parse/guided/quick-pair flows, CLI routing
├── test_labels.py        # Unit tests: read_identity + format_label composition (spec-pinned)
.env.example              # Tracked template for .env (placeholder values only)
.env                      # Local config (AWC_DEFAULT_IP) — gitignored, never commit
pyproject.toml            # Packaging; console scripts: awc, adb-connect; pytest pythonpath=src
README.md                 # Human-facing usage docs
AGENTS.md                 # This file
docs/                     # Topic docs (whitelisted in .gitignore) — see Documentation Map
docs/superpowers/         # AI working docs (specs/plans) — gitignored, never commit
```

## Documentation Map

| Doc | Contents |
|-----|----------|
| [README.md](README.md) | Overview, features, install, usage examples |
| [docs/README.md](docs/README.md) | Documentation index |
| [docs/architecture/](docs/architecture/README.md) | Pipeline stages, mDNS service types, watch state machine, QR fallback chain, port scan |
| [docs/configuration/](docs/configuration/README.md) | Flag reference, `.env` + `AWC_*` vars, extras, cross-platform notes |

## Module Responsibilities

| Module | Area | Functions | Purpose |
|--------|------|-----------|---------|
| `qr.py` | QR pairing | `generate_code`, `generate_device_name`, `format_qr_data` | Build WIFI:T:ADB credentials |
| `qr.py` | QR rendering | `display_qr` (modes auto/sixel/ascii), `render_ascii_qr_lines`, `_display_qr_ascii`, `_render_sixel_qr`, `supports_sixel`, `_query_device_attrs` | Sixel pixel QR with **guaranteed ASCII fallback** (missing dep, unsupported terminal, probe/render error, Windows); CLI default is `ascii` because DA1 can advertise Sixel without rendering (Konsole); `render_ascii_qr_lines` returns the payload as plain lines so the curses selector can redraw the QR in-screen (ASCII only — a Sixel image cannot be repainted inside curses) |
| `adb.py` | ADB wrappers | `run_adb_pair`, `run_adb_connect`, `run_adb_disconnect(ip, short_name)`, `connect_with_retries`, `is_device_connected`, `connected_ips`, `connected_serials`, `read_identity`, `warm_adb`, `ADB_PATH` | **argv lists, no `shell=True`** (except the user hook); `ADB_PATH` configurable via `--adb-path`; `connected_serials()` = serials in state `device` per one `adb devices` run — both `ip:port` and mDNS-form (`adb-<short>._adb-tls-connect._tcp`) — and drives `is_connected` plus enrichment; `read_identity(serial)` reads `{marketname, brand, model, device_name}` in one quiet `getprop`/`settings` call (None on failure); `run_adb_disconnect` disconnects by **exact serial** (every `ip:` form + the exact mDNS serial) and returns only serials adb really removed, so `Disconnected:` lines are truthful; `warm_adb()` starts the adb server in the background at CLI startup |
| `adb.py` | Hook | `run_on_connect` | Runs `--on-connect` shell command with `$AWC_TARGET` set; failures are non-fatal |
| `adb.py` | Port scan | `scan_for_adb_port` | Parallel coarse (common ports + 30000–48000 sampled) then fine scan when mDNS gives no port; first successful `adb connect` wins |
| `mdns.py` | mDNS discovery | `discover_pairing_service`, `discover_connection_port`, `discover_all_phones`, `_resolve_host`, `_short_service_name`, `_browse` | zeroconf listeners for `_adb-tls-pairing._tcp` and `_adb-tls-connect._tcp`; `_browse` is the shared browse helper (timeout / stop-signal / grace window); `discover_all_phones` returns **6-tuples** `(ip, short_name, is_connected, is_paired, connect_port, identity)` — instance names shortened at the source, `is_connected` matched against both serial forms via `connected_serials()`, `connect_port` from the connect record (None when only pairing was seen), `identity` a reserved slot (always None at discovery — filled later by `flow.enrich_devices`) |
| `ui.py` | Prompts | `ask_pairing_code(allow_empty)`, `ask_pairing_port`, `ask_connection_port(optional)`, `ask_ip`, `_ask_port`, `_status`, `poll_stdin` | Interactive input with validation; `_ask_port` is the shared validated TCP-port prompt (optional mode lets Enter mean "auto"); `_status` = Connected > Paired > Unpaired > Not connected; `poll_stdin(timeout)` = non-busy stdin readiness (select on POSIX, kbhit on Windows; never swallows Ctrl+C) so wait loops drain scans instead of spinning |
| `ui.py` | QR wait | `wait_for_pairing(discovery_done, timeout=None)` | **Legacy/compat** API (kept exported; flow's live waits consume session events via `flow.wait_for_scanned_event` instead). Waits for scan/Enter; returns "discovered"/"manual", and "timeout" **only when a caller passes an explicit timeout** — the default `timeout=None` waits indefinitely (no countdown). Polls instead of `select()` on Windows (no Enter shortcut there) |
| `ui.py` | Selection UI | `select_devices`, `_run_interactive_selection`, `_curses_selection`, `_run_noninteractive_selection`, `_print_device_list`, `format_label` | Curses checkbox UI (TTY) or comma-separated input (non-TTY), falls back if curses fails; `_print_device_list` prints the discovered-device list; `format_label(rec)` renders the row label from identity — `Human Name (model) · ip short_name` (falls back to `ip short_name` without identity, never raises) |
| `flow.py` | Connection flow | `connect_single_device`, `connect`, `_connect_inner`, `wait_for_scanned_event`, `_verify_scanned_event`, `PairingSession`, `start_pairing_session`, `enrich_devices`, `parse_direct_target`, `direct_connect`, `SCAN_TICK` | Full flow: pair → discover port → connect → scan fallback; all success paths go through `success()` → hook. `PairingSession` renders the QR **immediately** at startup (lyto-style: credentials are local, discovery runs behind it) and listens for the scan **for the whole session**: every scan becomes an `events` queue payload (`scanned` with ip/short/port/target/identity, or retryable `scan-failed`); **the LISTENER owns pairing** — it runs pair + connect + identity read before queueing; `done`/`result` record only the FIRST scan as legacy state. `stop()` ends the listener within one browse tick. **No `consumed` flag** — the session outlives any single target. `wait_for_scanned_event(session)` is `connect_single_device`'s pairing wait: drains fresh queue events only (an already-set `done` never satisfies it), verifies the transport (verify → one quiet reconnect → re-verify; dead → retry line, keep waiting), Enter → "manual", EOF stdin paced not spammed — it NEVER re-pairs and never keys on `done`/`result`. `SCAN_TICK = 2` is the silent listener's browse tick (the only session timing constant — not user-facing). `enrich_devices(devices)` fills identity (slot 5) post-discovery: parallel, best-effort, never disconnects, prints its banner only when a candidate exists. `direct_connect` = `--direct` manual/remote path: connect-first by address, prompt-pair only when unreachable |
| `watch.py` | Watch mode | `WatchListener`, `watch_cycle`, `watch_loop`, `_start_watch_browsers`, `_watch_rescan` | Daemon: tracks advertised state (True=paired/"pair"/False=gone), reconnects cycles, safety-net rescan on non-woken timer cycles (`watch_loop(poll_interval=30)`, seconds — event-woken cycles skip it), lenient record unpacking (extra slots beyond the ip are ignored, so 6-tuples pass straight through), browser cleanup on exit |
| `env.py` | Env config | `load_env`, `_parse_env_file` | Loads `AWC_*` vars from `.env` files at `main()` start; real env > `./.env` > `~/.config/awc/.env`; never overrides |
| `cli.py` | CLI | `main`, `run`, `_guarded`, `_drain_scan_events`, `_early_scan_target`, `_drain_one_scan`, `_absorb_scanned`, `_verify_scanned`, `_finish_scanned` | argparse: `--ip --port --direct --skip-pair --manual --wait --all --list --debug --qr --on-connect --watch --adb-path` (`--wait` keeps the single-device prompt — a ready record (connected/paired) otherwise connects immediately; help epilog documents every `--direct` form; `--qr` help notes the in-selector QR is always ASCII — a Sixel image cannot be redrawn inside curses); `run()` is the console entry (drops return values so `sys.exit` never dumps reprs / exits 0 on success) **and nets Ctrl+C that escapes `main()`** — e.g. during discovery, enrichment, or the 0-device wait (the selector itself sits behind `main()`'s `_guarded` phase guard) — into a clean `Cancelled.` exit 1; `_guarded` does the same around each connect call; scan events are drained before selection (`_early_scan_target`), in the 0-device wait and the 1-device prompt (`_drain_one_scan`), and before each batch connect (`_absorb_scanned`); selection is desired-state (unchecked connected devices get disconnected by exact serial); `--direct` conflicts with `--ip` |
| `connect.py` | Facade | re-exports | Re-exports names from the modules above so existing `adb_wifi_connect.connect.X` imports keep working; never a patch target |

## Data Flow

1. **QR first** (no-arg, pairing possible) — `start_pairing_session()` renders the QR **before anything else** (local operation) and starts the session-long silent listener; discovery and adb warm-up run behind it. Every scan becomes an event on the session's queue, routed to whatever is waiting (step 3)
2. **Discover → enrich** — `discover_all_phones()` listens on mDNS for both pairing and connect services → **6-tuples** `(ip, short_name, is_connected, is_paired, connect_port, identity)` (`is_connected` recognizes both serial forms — `ip:port` and `adb-<short>._adb-tls-connect._tcp`; identity is a reserved None slot at discovery); returns `grace` (1s) after the first hit, checked on the 0.5s poll. Then `enrich_devices()` fills identity for paired records (`Reading device names…` banner; parallel, best-effort, never disconnects)
3. **Drain → route** — queued scans drain first (`_early_scan_target`: a verified scan already is the target — the selector never appears). Then: **0 devices** → fail fast when no session is live (`--list`/`--manual`/`--skip-pair`/`--watch`/pipe), otherwise wait **indefinitely** for a verified scan (stray Enter is consumed and discarded, Ctrl+C quits); **`--list`** → print labels + status, exit; **1 device** → status line, then on a TTY an IMMEDIATE connect when the record is ready (connected/paired) — no prompt; `--wait`, an unready record, or a failed eager connect → Enter-to-connect prompt (scans drain while it waits; a pipe connects straight away); **2+ devices** → curses selector that **draws the QR above the list** (ASCII lines, all-or-nothing fit check) and treats a scan as confirm
4. **Select** — selection is **desired-state**: discovered but unchecked devices that are connected get `adb disconnect`ed by exact serial (`Disconnected:` printed only for serials adb really removed)
5. **Pair** (skipped with `--skip-pair`; never reached for paired records — `connect_single_device(paired=True)` skips the QR wait entirely) — `flow.wait_for_scanned_event(session)` consumes **fresh** queue events (an already-set `done` never satisfies it; Enter = manual): a `scanned` event's payload IS the target (verified transport → connection phase on its ip/port — **no re-pair**: the listener already paired, and a successful pair deregisters the pairing service), `scan-failed`/dead transports print their retry lines and the wait continues. The session lives for the whole run — each queued scan serves exactly ONE wait; `done`/`result` remain first-wins legacy state, with **no consumed flag** to reset
6. **Connect** — explicit `--port` → mDNS connect-service discovery → parallel port scan (30000–48000 coarse + fine) → common ports (5555/5556) → manual port prompt
7. **Verify** — `is_device_connected()` reconnects stale entries and runs `adb shell echo test` to confirm the device responds
8. **Hook** — every success path calls `run_on_connect(on_connect, "ip:port")`
9. **Watch** (`--watch`) — `watch_loop`: zeroconf `WatchListener` tracks per-IP state; each cycle reconnects down devices (silently if the connect service advertises = already paired, full pair flow otherwise); a safety-net rescan runs on non-woken timer cycles only (`poll_interval=30`, seconds); Ctrl+C → cleanup browsers
10. **Direct** (`--direct [IP[:PORT] [CODE]]`) — alternate entry, no discovery/QR: connect-first by address (explicit port → mDNS → scan), prompt-pair only when unreachable; with a code, pair first (target port = pairing port, `--port` = connect port). Composes with `--watch`/`--debug`; see docs/configuration

## Common Tasks

```bash
# Run tests (must pass before committing) — 259 tests, no network/devices
python -m pytest -q

# Install for development with all optional deps (sixel QR included)
uv tool install --editable ".[all]"    # or: pip install -e ".[all]"

# Run the CLI (installed)
awc                     # interactive multi-device (adb-connect is the same)
awc --list

# Verify entry point after install
awc --help
```

## Conventions

- **Dependencies:** stdlib only required; `qrcode`, `zeroconf`, `sixel` are optional and import-guarded — code must degrade gracefully without them (tests cover both paths). Sixel rendering: any failure at any stage → ASCII fallback, never crash.
- **adb calls:** `subprocess.run(argv_list, ...)` with explicit `timeout=` and **no `shell=True`** (argv lists keep `--adb-path` values with spaces working on Windows). `shell=True` is used only for the user-supplied `--on-connect` command.
- **Logging:** module `log = logging.getLogger(__name__)`; `--debug` enables DEBUG (adb argv/results, mDNS events).
- **Tests mock subprocess/input** — no real devices needed; 259 tests, no network. Real-Sixel render test auto-skips when the `sixel` extra isn't installed. pytest gets `src/` on the path via `[tool.pytest.ini_options] pythonpath` — no sys.path hacks in tests.
- **Patch targets:** tests patch the namespace where the subject function looks a name up (`adb_wifi_connect.flow.X`, `adb_wifi_connect.mdns.Zeroconf`, `adb_wifi_connect.cli.watch_loop`, …); `connect.py` is a facade and is never a patch target.
- **Import gotcha:** `adb_wifi_connect.__init__` re-exports the `connect()` *function*, which shadows the *submodule* attribute `adb_wifi_connect.connect`. String patch targets (`patch('adb_wifi_connect.flow.X')` and friends) resolve correctly via importlib; to get the facade module object in code use `sys.modules["adb_wifi_connect.connect"]` — never `import adb_wifi_connect.connect as connect` (that binds the function).
- **Entry points:** `awc` and `adb-connect` console scripts call `cli:run` (a wrapper that drops `main()`'s return value — never hand returns to `sys.exit()`: it dumps the repr and exits non-zero on success) and nets any `KeyboardInterrupt` that escapes `main()` (e.g. during discovery, enrichment, or the 0-device wait — the selector sits behind `main()`'s `_guarded` phase guard) into a clean `Cancelled.` message + exit 1 instead of a traceback; `python -m adb_wifi_connect` calls `main()` directly (expression statement discards the return). Exit codes: 0 success, 1 failure.
- **Output rules:** each device gets exactly one final status line (`Already connected:` *or* `Connected:` — flow prints it, CLI never reprints results); device headers use shortened mDNS names; no bare result echoes.
- **No countdowns:** interactive waits never show a window or tick down. The QR session's listener runs silently for the whole session at `flow.SCAN_TICK = 2` (its browse cadence — the only session timing constant, never printed); the live pairing wait `flow.wait_for_scanned_event` is timerless (no timeout argument at all — it ends on a verified scan or Enter), as is the legacy `ui.wait_for_pairing` (`timeout=None` default); the 0-device wait ends only on a verified scan or Ctrl+C; paired devices skip the QR wait entirely. Never hardcode or print a timeout in user-facing messages.
- **Device records:** discovery returns 6-tuples `(ip, short_name, is_connected, is_paired, connect_port, identity)`; consumers unpack leniently (`rec[:3]` + guarded slots, legacy 3-tuples still work). `identity` (slot 5) is None at discovery and filled by `flow.enrich_devices` → `{marketname, brand, model, device_name}` or None; labels come from `ui.format_label` — `Human Name (model) · ip short_name` (identity prefers marketname, then device_name, then brand+model; degrades to `ip short_name` without it). Status priority: Connected > Paired > Unpaired > Not connected (see `ui._status`).
- **Build backend:** `setuptools.build_meta` (the `._legacy` backend does not exist — do not change back).
- **Config:** no hardcoded defaults for network settings — `AWC_DEFAULT_IP` comes only from the environment/`.env` (precedence: real env > `./.env` > `~/.config/awc/.env`, loader never overrides). `.env` is gitignored (may contain real IPs); `.env.example` holds placeholders only.
- **Docs:** tracked docs = `README.md`, `AGENTS.md`, `docs/README.md`, `docs/architecture/`, `docs/configuration/` (whitelisted in `.gitignore`); `docs/superpowers/` is gitignored and must never enter git history. Keep the Documentation Map above in sync when adding topic docs.
