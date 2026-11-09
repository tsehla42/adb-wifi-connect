import sys
import string
import random
import time
import logging
import os
import select
import re
import io
import shutil
import contextlib

log = logging.getLogger(__name__)

try:
    import qrcode
except ImportError:
    qrcode = None

QR_FORMAT = "WIFI:T:ADB;S:{name};P:{password};;"


def generate_code(length=6):
    """Generate random alphanumeric code."""
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choice(chars) for _ in range(length))


def generate_device_name(prefix="debug"):
    """Generate device name with random suffix."""
    suffix = generate_code(4)
    return f"{prefix}_{suffix}"


def format_qr_data(name, password):
    """Format pairing credentials as WiFi QR data."""
    return QR_FORMAT.format(name=name, password=password)


def display_qr(text, mode="auto"):
    """Display pairing QR in the terminal.

    mode "auto": Sixel pixel rendering when the terminal supports it,
    ASCII otherwise. mode "sixel": force Sixel (falls back to ASCII with
    a notice). mode "ascii": always ASCII. Any Sixel failure at any stage
    falls back to ASCII — this never raises.
    """
    if qrcode is None:
        print("\n[QR not available — install qrcode: pip install qrcode]")
        print(f"QR data: {text}\n")
        return

    if mode == "ascii":
        log.debug("QR mode: ascii (forced)")
        return _display_qr_ascii(text)

    if mode == "sixel":
        log.debug("QR mode: sixel (forced)")
        try:
            print(_render_sixel_qr(text))
            return
        except Exception:
            print("[Sixel rendering failed — falling back to ASCII QR]")
        return _display_qr_ascii(text)

    # auto
    supported = supports_sixel()
    log.debug("QR mode: auto -> %s (terminal %s Sixel)",
              "sixel" if supported else "ascii",
              "advertises" if supported else "does not advertise")
    if supported:
        try:
            print(_render_sixel_qr(text))
            return
        except Exception:
            log.debug("Sixel render failed, falling back to ASCII", exc_info=True)
    return _display_qr_ascii(text)


def render_ascii_qr_lines(text):
    """Raw QR ASCII art lines for text — no printing, no centering/padding.

    Returns [] when the optional qrcode dependency is missing.
    """
    if qrcode is None:
        return []
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=1
    )
    qr.add_data(text)
    qr.make(fit=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        qr.print_ascii(invert=True)
    return buf.getvalue().splitlines()


def _display_qr_ascii(text):
    """Render QR as inverted ASCII art, centered in the terminal.

    Size ladder (cannot fail): COLUMNS/LINES env -> OS terminal ioctl
    (works on Linux/macOS/Windows) -> (80, 24). Negative padding clamps
    to 0, so a QR wider than the reported width prints intact, flush left.
    """
    lines = render_ascii_qr_lines(text)
    if not lines:
        return
    term = shutil.get_terminal_size(fallback=(80, 24))
    width = max(len(line) for line in lines)
    pad = max(0, (term.columns - width) // 2)
    vpad = max(0, (term.lines - len(lines)) // 2)
    print("\n" * vpad + "\n".join(" " * pad + line for line in lines))


def _query_device_attrs():
    """Query the terminal's primary device attributes (ESC[c).

    Returns the raw response string, or None if unavailable (not a TTY,
    probe error, missing termios, etc.). Never raises.
    """
    try:
        if not (sys.stdin.isatty() and sys.stdout.isatty()):
            return None
        import termios, tty
        fd = sys.stdin.fileno()
        old_attrs = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            os.write(fd, b"\x1b[c")
            response = b""
            deadline = time.time() + 0.3
            while time.time() < deadline and b"c" not in response:
                ready, _, _ = select.select([fd], [], [], 0.1)
                if not ready:
                    continue
                chunk = os.read(fd, 64)
                if not chunk:
                    break
                response += chunk
            return response.decode("ascii", "replace") if response else None
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_attrs)
    except Exception:
        return None


def supports_sixel():
    """True if this terminal advertises Sixel graphics (DA attribute 4)."""
    if os.name == "nt":
        return False
    response = _query_device_attrs()
    if not response:
        return False
    match = re.match(r"\x1b\[\?([0-9;:]+)c", response)
    if not match:
        return False
    return "4" in re.split(r"[;:]", match.group(1))


def _render_sixel_qr(text):
    """Render pairing QR as a Sixel escape string.

    Requires the `sixel` extra (sixel + qrcode[pil]). Raises on failure
    so callers can fall back to ASCII.
    """
    import sixel
    qr = qrcode.QRCode(border=1, box_size=10, version=1)
    qr.add_data(text)
    qr.make(fit=True)
    image = qr.make_image(back_color="white", fill_color="black")
    buffer = io.BytesIO()
    image.save(buffer)
    buffer.seek(0)
    return sixel.converter.SixelConverter(buffer, chromakey=False).getvalue()
