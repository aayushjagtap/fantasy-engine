"""util/console.py -- shared guard for demo output on narrow-codepage consoles.

Player names routinely contain characters outside Windows' default console
codepage (cp1252) -- Dončić, Jokić, Šengün, etc. -- which crashes a bare
print() with UnicodeEncodeError. Call configure_stdout_utf8() once near the
top of any demo/__main__ entry point that prints player names.
"""
from __future__ import annotations

import sys


def configure_stdout_utf8() -> None:
    """Reconfigure stdout/stderr to UTF-8, replacing unencodable characters
    instead of crashing. No-op where reconfigure() isn't available (Python <3.7)
    or the stream already reports utf-8."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure") and (stream.encoding or "").lower() != "utf-8":
            stream.reconfigure(encoding="utf-8", errors="replace")
