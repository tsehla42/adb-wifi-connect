import os


def _parse_env_file(path):
    """Parse a KEY=VALUE .env file into a dict. Missing file -> {}.
    Handles comments, blank lines, whitespace, and quoted values."""
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return {}
    values = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


def load_env(environ=None, cwd=None, home=None):
    """Fill AWC_* variables from .env files.

    Real environment variables always win; files only supply what is
    unset. Among files, ./.env wins over ~/.config/awc/.env.
    """
    if environ is None:
        environ = os.environ
    if cwd is None:
        cwd = os.getcwd()
    if home is None:
        home = os.path.expanduser("~")
    candidates = [
        os.path.join(home, ".config", "awc", ".env"),
        os.path.join(cwd, ".env"),
    ]
    values = {}
    for path in candidates:
        values.update(_parse_env_file(path))
    for key, value in values.items():
        if key.startswith("AWC_"):
            environ.setdefault(key, value)
