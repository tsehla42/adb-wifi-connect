# Architecture

How adb-wifi-connect works. Single-package design: nine focused modules
under `src/adb_wifi_connect/`, with `connect.py` as a re-export facade
that keeps older import paths working.

| Module | Contents |
|--------|----------|
| `env.py` | `.env` loader (`AWC_*` vars) |
| `qr.py` | QR credentials + Sixel/ASCII rendering |
| `adb.py` | subprocess wrappers, `ADB_PATH`, port scan, `run_on_connect` |
| `mdns.py` | zeroconf discovery, shared `_browse` helper |
| `ui.py` | prompts, device selection UI, `wait_for_pairing` |
| `flow.py` | connect pipeline (`connect`, `connect_single_device`) |
| `watch.py` | watch daemon |
| `cli.py` | argparse setup + `main()` |
| `connect.py` | re-export facade (import compatibility) |

## Entry points

| Invocation | Resolves to |
|------------|-------------|
| `awc` / `adb-connect` | console scripts → `cli:run()` (drops `main()`'s return so `sys.exit` never dumps reprs; exit 0 on success, 1 on failure) |
| `python -m adb_wifi_connect` | `__main__.py` → `main()` (return value discarded by the expression statement) |

## Discovery pipeline

```
QR (t≈0)  →  discover  →  enrich  →  drain  →  route  →  pair  →  connect  →  verify  →  hook
```

1. **QR first** (no-arg mode, pairing possible) — `start_pairing_session()`
   renders the QR immediately (credentials are pure local randomness —
   nothing to wait for) and starts the session-long silent listener;
   discovery and the background `adb start-server` warm-up run behind it.
   The listener browses at `SCAN_TICK` (2s cadence) for the whole session
   and turns every scan into an `events` queue payload — routing happens
   at the waiting sites below.
2. **Discover** — zeroconf listens on both ADB service types and returns
   6-tuples `(ip, short_name, is_connected, is_paired, connect_port,
   identity)`. `is_connected` recognizes both live serial forms
   (`ip:port` and `adb-<short>._adb-tls-connect._tcp`); `identity` is a
   reserved None slot at discovery. Returns after a 1s grace window from
   the first device seen (checked on the 0.5s poll) so slower devices
   are still collected. Status column priority: Connected >
   Paired > Unpaired > Not connected.
3. **Enrich** — `enrich_devices()` fills `identity` (slot 5) for paired
   records right after discovery: one `adb devices` snapshot, then
   parallel best-effort reads over the live serial (connecting a paired
   device first if needed, 3s cap, quiet). Prints `Reading device names…`
   only when a candidate exists; never raises, never disconnects, never
   mutates the discovery snapshots. `ui.format_label` turns identity into
   the row label: `Human Name (model) · ip short_name`.
4. **Drain** — queued scans are served first (`_early_scan_target`): a
   verified scan already is the target, so it is announced and the run
   ends before any selection UI could appear for other phones.
5. **Route** — **0 devices**: fail fast when no session is live
   (`--list`/`--manual`/`--skip-pair`/`--watch`/pipe); with a QR session
   on screen, wait **indefinitely** for a verified scan (a stray Enter is
   consumed and discarded; Ctrl+C quits — no countdown anywhere).
   **`--list`**: print labels + status, exit. **1 device**: one status
   line, then on a TTY an IMMEDIATE connect when the record is ready
   (connected/paired) — no prompt; `--wait`, an unready record, or a
   failed eager connect → Enter-to-connect prompt (scans drain while it
   waits; a pipe connects straight away). **2+ devices**: the curses
   selector — which draws the session's QR above the list (see below)
   and treats a scan as confirm.
6. **Select / desired state** — checkboxes on a TTY, numbered input
   otherwise; curses import/render failure falls back to numbered input.
   Selection is **desired-state**: discovered but unchecked devices that
   are currently connected get `adb disconnect`ed **by exact serial**
   (`ip:` forms + the exact mDNS serial), and a `Disconnected:` line is
   printed only for serials adb really confirmed as removed.
7. **Pair** — skipped with `--skip-pair`, and never reached for
   already-paired records (`connect_single_device(paired=True)` skips the
   QR wait entirely). Otherwise the live session's credentials are reused
   (never re-rendered); manual 6-digit code as fallback (see below).
8. **Connect** — resolution order:
   explicit `--port` → mDNS `_adb-tls-connect` port → parallel port scan
   (coarse then fine) → common ports 5555/5556 → manual port prompt.
9. **Verify** — `adb devices` entry is refreshed (disconnect + reconnect)
   and confirmed with a real `adb shell echo test` round-trip.
10. **Hook** — every success path runs `--on-connect` with `$AWC_TARGET`
    set to the connected `ip:port`.

## mDNS service types

| Type | Meaning |
|------|---------|
| `_adb-tls-pairing._tcp.local.` | Phone is waiting to be paired (QR scan or pairing code) |
| `_adb-tls-connect._tcp.local.` | Phone is already paired and advertising its connection port |

The connect service appearing means pairing is no longer needed — watch
mode uses this to reconnect silently instead of showing a QR code again.

## Pairing flow (QR → manual)

1. **Session starts instantly** (no-arg mode): `PairingSession` generates
   credentials, renders the `WIFI:T:ADB;...` QR at t≈0, and starts a
   background listener — lyto-style, nothing is discovered first. The
   listener browses one silent tick at a time (`SCAN_TICK`, 2s — its
   cadence, never printed to the user) for as long as the session lives,
   **pairs every scan itself** (runs `adb pair`, resolves that phone's
   connect port, `adb connect`s it, reads its identity), then queues
   the outcome as an `events` payload (`scanned` with
   ip/short/port/target/identity, or a retryable `scan-failed` when the
   pair/port step fails), and never prints itself.
2. Waiting sites consume the queue **fresh** — never `done`/`result`:
   - `flow.wait_for_scanned_event(session)` is the foreground pairing
     wait inside `connect_single_device`:
     - **scanned** — takes ip/port/target from the event payload and
       verifies the transport (echo-test → one quiet `adb connect`
       retry → re-verify); a dead transport prints
       `Scan paired … but connect failed — scan again` and the wait
       continues. **Never re-pairs**: the listener already paired, and
       a successful pair deregisters the pairing service immediately,
       so a second pair would fail against a closed port. A scan of a
       *different* phone than selected wins — it becomes the
       connection target.
     - **scan-failed** — prints `Pairing failed for <ip> — scan again`
       and the wait continues (the listener is still serving scans).
     - **manual** — user pressed Enter.
     Events are consumed **once**: a wait whose queue is empty blocks
     for a NEW event — an already-set `done` never satisfies it, so a
     stale scan can never hijack a later target.
   - cli's drains (`_early_scan_target`, `_drain_one_scan`,
     `_absorb_scanned`) and the selector's idle-tick absorb serve their
     own sites from the same payloads.
   The foreground wait is timerless — no countdown, no window, no
   timeout text. Already-paired devices never reach it
   (`connect_single_device(paired=True)` skips the whole pairing
   branch).
3. Manual mode prompts for the 6-digit code and pairing port from the phone.
4. The session lives for the **whole run**, not one target: each queued
   scan serves exactly ONE waiting site; `done` and `result` record
   only the first success (first-wins legacy state — later scans still
   queue their own events but never overwrite it), so one QR serves
   every waiting site — discovery, the prompts, the selector, the batch
   loop. There is no `consumed` flag and no stale QR to replace.
   `stop()` ends the listener within the current browse tick.

**QR inside the selector:** with multiple devices, the session's QR
payload is redrawn above the device list — ASCII lines only, all-or-nothing:
the block is drawn only when it renders *and* the full frame fits the
terminal — height (`qr lines + devices + 8 + (1 if status) ≤ rows`) AND
width (no QR line and no device row may exceed the columns: an over-long
line wraps in ncurses and consumes rows the height term never counted),
otherwise the list runs without it. A Sixel image cannot be repainted
inside curses, so the in-selector QR is always ASCII regardless of
`--qr`. A scan while the selector is open acts as confirm: the phone is
appended to the list, force-checked, and returned immediately.

The foreground wait interleaves `ui.poll_stdin` (select on POSIX, kbhit
on Windows) with bounded queue gets — pairing still completes on any
platform, and an EOF stdin is noticed once and paced with sleep instead
of re-polled. The legacy `ui.wait_for_pairing` keeps its own Windows
fallback (it polls the discovery event — no Enter shortcut there).

## Direct path (`--direct`)

The manual/remote alternative — no discovery session, no QR:

1. Resolve `ip[:port]` (prompted, `AWC_DEFAULT_IP` prefilled, when bare).
2. Already connected? → done (no prompts fire).
3. **With a code**: pair first — target port = pairing port, `--port`
   (if given) = connection port.
4. **Without a code**: connect-first — explicit port → mDNS connect
   service → scan. Only when unreachable: guided pairing prompts
   (code + pairing port, Enter aborts).
5. After pairing: connection port from `--port`, else prompted
   (Enter = auto-discover/scan, then a strict prompt).
6. Same verify + `--on-connect` hook as the QR path; composes with
   `--watch` (connect once, then keep-alive) and `--debug`.

## Watch mode (`--watch`)

Per-device state tracked from mDNS events:

| State | Meaning | Watch does |
|-------|---------|------------|
| `True` | connect service advertised → already paired | reconnect silently (skip pairing) |
| `"pair"` | only pairing service advertised | run the full pair flow |
| `False` | services removed → phone gone / wireless debugging off | skip until it returns |
| *never seen* | listener missed it | reconnect using your `--skip-pair` setting |

- Service **added** → wake event fires → next cycle runs immediately
  (no fixed sleep).
- Service **removed** → state `False`; a rescan on the next **non-woken
  timer cycle** (`watch_loop(poll_interval=30)` — seconds; event-woken
  cycles already know the phone is back) is the safety net for missed
  removal events.
- Watch code unpacks discovery records **leniently** — the rescan reads
  only the ip slot, extra slots are ignored — so the 6-tuple shape (and
  any future slots) pass through without changes.
- A silent (already-paired) reconnect attempt that fails downgrades the
  state to `"pair"`, so the next cycle offers the pairing flow.
- Ctrl+C exits cleanly (browsers cancelled, zeroconf closed).

## QR rendering

`--qr auto|sixel|ascii` (default `ascii`):

```
auto → terminal supports Sixel? ─┬─ yes → render pixel QR ── error? ─┐
       (DEC DA probe: ESC[c)     └─ no / no extra / probe fails      │
                                                                     ▼
                                              ASCII QR (never crashes)
sixel → force pixel QR, notice + ASCII on any failure
ascii → ASCII directly
```

The Sixel probe requires a POSIX TTY; Windows and non-TTYs go straight
to ASCII. Inside the curses selector the QR is always drawn as ASCII art
(`qr.render_ascii_qr_lines`) — a pixel image cannot be repainted on every
redraw — see "QR inside the selector" above.

## Port scanning

Used when mDNS doesn't reveal a connection port:

1. **Coarse** — common ports (5555, 5556) plus 30000–48000 sampled every
   100th port, 20 parallel workers, bounded by the scan timeout.
2. **Fine** — if the coarse hit was outside the common ports, rescan
   ±50 around it with 10 workers (5s bound).
3. The first successful `adb connect` wins; a failed fine scan falls back
   to the coarse result.
