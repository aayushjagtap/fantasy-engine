"""tests/test_crosswalk.py -- moved from crosswalk/spike.py's --selftest.

Not one of BUILD_PLAN's "five" selftest blocks, but pure/offline like the
rest, so it gets the same treatment for consistency.
"""
from __future__ import annotations

from crosswalk.spike import normalize_name

CASES = {
    "Luka Dončić": "lukadoncic",
    "Karl-Anthony Towns": "karlanthonytowns",
    "Jaren Jackson Jr.": "jarenjackson",
    "De'Aaron Fox": "deaaronfox",
    "P.J. Washington": "pjwashington",
    "Nikola Jokić": "nikolajokic",
    "Bogdan Bogdanović": "bogdanbogdanovic",
    "Bojan Bogdanović": "bojanbogdanovic",
}


def test_normalize_name_expected_keys():
    for raw, expected in CASES.items():
        got = normalize_name(raw)
        assert got == expected, f"{raw!r} -> {got!r}, expected {expected!r}"


def test_normalize_name_keeps_near_duplicates_distinct():
    assert normalize_name("Bogdan Bogdanović") != normalize_name("Bojan Bogdanović")
