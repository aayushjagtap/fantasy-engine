"""tests/test_console.py -- util/console.py's stdout/stderr UTF-8 guard."""
from __future__ import annotations

from util.console import configure_stdout_utf8


def test_configure_stdout_utf8_does_not_raise():
    configure_stdout_utf8()  # idempotent, safe to call repeatedly (e.g. under pytest capture)
    configure_stdout_utf8()
