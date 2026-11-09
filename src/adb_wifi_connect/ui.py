import os
import queue
import select
import shutil
import sys
import time

from .qr import render_ascii_qr_lines


def ask_pairing_code(allow_empty=False):
    """Prompt for 6-digit pairing code from phone.
    With allow_empty, Enter returns None (skip / give up)."""
    while True:
        code = input("  Pairing code: ").strip()
        if code:
            return code
        if allow_empty:
            return None
        print("  Code cannot be empty.")


def _ask_port(label, optional=False):
    """Prompt for a validated TCP port; the label renders the prompt text.
    With optional, Enter returns None instead of re-asking."""
    while True:
        raw = input(f"  {label}: ").strip()
        if not raw and optional:
            return None
        try:
            port = int(raw)
            if 1 <= port <= 65535:
                return port
            print("  Port must be 1-65535.")
        except ValueError:
            print("  Please enter a number.")


def ask_pairing_port():
    """Prompt for pairing port from phone."""
    return _ask_port("Pairing port")


def ask_connection_port(optional=False):
    """Prompt for connection port (different from pairing port).
    optional: Enter returns None (caller falls back to auto-discovery)."""
    label = ("Connection port (Enter = auto-discover/scan)"
             if optional else "Connection port")
    return _ask_port(label, optional=optional)


def ask_ip():
    """Prompt for a device IP/hostname. AWC_DEFAULT_IP is offered as the
    default (Enter accepts it)."""
    default = os.environ.get("AWC_DEFAULT_IP")
    hint = f" [{default}]" if default else ""
    while True:
        raw = input(f"  Device IP{hint}: ").strip()
        if raw:
            return raw
        if default:
            return default
        print("  IP cannot be empty.")


def select_devices(devices, is_tty=False, qr_text=None, session=None):
    """
    Show device list and let user select which to connect.
    Returns list of selected indices.
    qr_text (the session's QR payload) and session thread the on-screen
    QR + live scan events into the interactive selector.
    """
    if len(devices) == 1:
        return [0]

    if is_tty:
        return _run_interactive_selection(devices, qr_text, session)
    else:
        return _run_noninteractive_selection(devices)


def _status(rec):
    """Status text for a device record: Connected > Paired > Unpaired.
    Legacy 3-tuples (no paired flag) stay generic."""
    if rec[2]:
        return "Connected"
    if len(rec) > 3:
        return "Paired" if rec[3] else "Unpaired"
    return "Not connected"


def format_label(rec):
    """Row label for a device record — never raises.

    With identity (rec[5]): "Brand Marketing (model) · ip short_name" —
    marketing = marketname or device_name; no marketing falls back to
    brand+model; identity without a model drops the parens; no identity
    (or a malformed record) degrades to "ip short_name".
    """
    try:
        ip, short = rec[0], rec[1]
    except Exception:
        return ""
    base = f"{ip} {short}"
    try:
        identity = rec[5] if len(rec) > 5 else None
        if not isinstance(identity, dict):
            return base
        brand = str(identity.get("brand") or "").strip()
        model = str(identity.get("model") or "").strip()
        marketing = str(identity.get("marketname") or "").strip()
        if not marketing:
            marketing = str(identity.get("device_name") or "").strip()
        if marketing and not marketing.lower().startswith(brand.lower()):
            name = f"{brand.capitalize()} {marketing}"
        elif marketing:
            name = marketing
        elif brand and model:
            name = f"{brand.capitalize()} {model}"
        else:
            name = model
        head = f"{name} ({model})" if model else name
        return f"{head} · {base}" if head else base
    except Exception:
        return base


def _print_device_list(devices):
    """Print the discovered-device list header and entries."""
    print(f"\nFound {len(devices)} devices:\n")
    for i, rec in enumerate(devices, 1):
        print(f"  {i}. {format_label(rec)}   {_status(rec)}")


def _run_noninteractive_selection(devices):
    """Non-interactive selection: parse comma-separated numbers."""
    _print_device_list(devices)
    print(f"\n  Enter device numbers to connect (comma-separated, or 'all'): ", end="")
    try:
        choice = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return []

    if choice == 'all':
        return list(range(len(devices)))

    selected = []
    for part in choice.split(','):
        part = part.strip()
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(devices):
                selected.append(idx)
    return sorted(set(selected))


def _run_interactive_selection(devices, qr_text=None, session=None):
    """Interactive selection with arrow keys and checkboxes."""
    try:
        import curses
    except ImportError:
        print("\n  [curses not available, falling back to keyboard input]")
        return _run_noninteractive_selection(devices)
    try:
        return _curses_selection(devices, qr_text=qr_text, session=session)
    except curses.error as e:
        print(f"\n  [curses error ({e}), falling back to keyboard input]")
        return _run_noninteractive_selection(devices)


def _curses_selection(devices, qr_text=None, session=None):
    """Curses-based interactive checkbox selection.

    qr_text renders the pairing QR above the list (all-or-nothing: only
    when it renders AND the full frame fits — height AND width; flush-
    left, ASCII lines only, a Sixel image cannot be redrawn inside
    curses). session drains scan events while keys are idle (200ms
    poll): a 'scanned' phone appends its record to THIS devices list,
    force-checks it, and returns immediately — the scan acts as
    confirm, cli's batch prints the row's single status line.
    'scan-failed' sets a status line and the selector keeps waiting.
    The devices list is never copied.
    """
    import curses

    qr_lines = render_ascii_qr_lines(qr_text) if qr_text else []

    selected = set()
    for i, rec in enumerate(devices):
        if rec[2]:
            selected.add(i)

    cursor = 0
    status = None

    def redraw(stdscr):
        stdscr.clear()
        stdscr.addstr("=== ADB WiFi Connection ===\n\n")
        rows = []
        for i, rec in enumerate(devices):
            checkbox = "[x]" if i in selected else "[ ]"
            prefix = "> " if i == cursor else "  "
            rows.append(f"{prefix}{checkbox} {i+1}. {format_label(rec)}   "
                        f"{_status(rec)}\n")
        # Fit check (exact): QR block only when it renders AND the FULL
        # frame fits — never a partial QR. Chrome 8 = the 7 drawn content
        # rows (header, blank, blank-after-QR, `Found` line, blank,
        # blank-before-hint, hint) + 1 for the cursor advance from the
        # hint's trailing newline: every payload keeps its '\n', so real
        # ncurses ERRs when drawn lines REACH the height (bottom-row
        # newline, scrollok off) — the frame needs height >= rows + 1.
        # +1 more when the scan-failed status line is present (it costs a
        # drawn row AND a spare row). WIDTH terms: ncurses WRAPS an over-
        # long line and every wrapped row is one the height term never
        # counted — a QR line wider than the terminal passes the height
        # check, wraps into hidden rows, and ERRs the draw out to the
        # comma fallback; drop the QR instead (list-only degradation).
        # Device rows get the same term: when THEY overflow the columns
        # the QR is never stacked on top of a frame whose real row count
        # is already wrong. Any check fails → QR omitted, list runs.
        size = shutil.get_terminal_size()
        rows_fit = all(len(row.rstrip("\n")) <= size.columns for row in rows)
        if (qr_lines and rows_fit
                and len(qr_lines) + len(devices) + 8 + (1 if status else 0)
                <= size.lines
                and max(len(line) for line in qr_lines) <= size.columns):
            for line in qr_lines:
                stdscr.addstr(line + "\n")  # flush-left, no padding
            stdscr.addstr("\n")
        stdscr.addstr(f"Found {len(devices)} devices:\n\n")
        for row in rows:
            stdscr.addstr(row)
        if status:
            stdscr.addstr(status + "\n")
        stdscr.addstr("\n  ↑/↓ move · Space toggle · Enter connect · scan the QR to add · Ctrl+C quit\n")
        stdscr.refresh()

    def _absorb_events():
        """One bounded drain of the session queue (get_nowait until
        Empty): 'scanned' appends (ip, short, True, True, port, identity)
        to devices, force-checks it, and returns True so the caller
        returns immediately; 'scan-failed' sets the status line.
        No session -> nothing to drain."""
        nonlocal status
        if session is None:
            return False
        while True:
            try:
                event = session.events.get_nowait()
            except queue.Empty:
                return False
            kind = event.get("kind")
            if kind == "scanned":
                devices.append((event.get("ip"), event.get("short"), True,
                                True, event.get("port"),
                                event.get("identity")))
                selected.add(len(devices) - 1)
                return True
            if kind == "scan-failed":
                status = f"  Pairing failed for {event.get('ip')} — try again"

    def main(stdscr):
        nonlocal cursor, status
        curses.curs_set(0)
        # 200ms poll so scan events can be drained while keys are idle.
        # Minimal test doubles without the method keep blocking getch.
        set_timeout = getattr(stdscr, "timeout", None)
        if set_timeout is not None:
            set_timeout(200)
        redraw(stdscr)

        while True:
            key = stdscr.getch()
            if key == -1:
                # Timeout tick: drain scan events — a scanned phone
                # returns immediately (scan acts as confirm)
                previous = status
                if _absorb_events():
                    return sorted(selected)
                if status != previous:
                    redraw(stdscr)
                continue
            if key == curses.KEY_UP and cursor > 0:
                cursor -= 1
            elif key == curses.KEY_DOWN and cursor < len(devices) - 1:
                cursor += 1
            elif key == ord(' '):
                if cursor in selected:
                    selected.discard(cursor)
                else:
                    selected.add(cursor)
            elif key in (curses.KEY_ENTER, 10, 13):
                break
            elif key == 3:  # Ctrl+C
                raise KeyboardInterrupt
            elif key == curses.KEY_RESIZE:
                pass  # redraw below picks up the new size
            redraw(stdscr)

        return sorted(selected)

    return curses.wrapper(main)


def poll_stdin(timeout: float) -> bool:
    """True when stdin has input ready within `timeout` seconds.

    POSIX: select() on stdin — truthiness of the ready list. Windows:
    a short sleep (wait_for_pairing's maintained nt cadence), then
    msvcrt.kbhit() — select() cannot watch stdin there. Never raises:
    an unusable stdin reads as 'not ready' after waiting out the
    timeout, so wait loops can never busy-spin. KeyboardInterrupt is
    never swallowed — the run() net owns Ctrl+C."""
    if os.name == "nt":
        time.sleep(min(timeout, 0.2))
        try:
            import msvcrt
        except ImportError:  # pragma: no cover - non-CPython on Windows
            return False
        return bool(msvcrt.kbhit())
    try:
        return bool(select.select([sys.stdin], [], [], timeout)[0])
    except (OSError, ValueError):
        # select() can't watch this stdin (e.g. test capture pipes) —
        # wait out the caller's interval and read as 'not ready'
        time.sleep(timeout)
        return False


def wait_for_pairing(discovery_done, timeout=None):
    """Wait for the phone to scan the QR, or for Enter (switch to manual).

    Returns "discovered" or "manual"; "timeout" only when an EXPLICIT
    timeout expires. The default timeout=None waits indefinitely — there
    is no timer unless a caller asks for one.
    On platforms where select() cannot watch stdin (Windows), polls the
    discovery event instead — no Enter-to-manual shortcut there.
    """
    deadline = time.time() + timeout if timeout is not None else None
    use_select = os.name != "nt"
    while True:
        if discovery_done.is_set():
            return "discovered"
        if deadline is not None and time.time() >= deadline:
            return "discovered" if discovery_done.is_set() else "timeout"
        if use_select:
            try:
                if sys.stdin in select.select([sys.stdin], [], [], 0.5)[0]:
                    sys.stdin.readline()
                    return "manual"
                continue
            except (OSError, ValueError):
                # select() can't watch stdin here (e.g. Windows) — poll instead
                use_select = False
                continue
        time.sleep(0.2)
