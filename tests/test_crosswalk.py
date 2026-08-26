"""tests/test_crosswalk.py -- crosswalk/names.py: normalization, the override
map, and key resolution. All pure/offline -- no nba_api, no network.
"""
from __future__ import annotations

import json

import pytest

from crosswalk.names import (
    collisions,
    load_overrides,
    normalize_name,
    resolve,
)

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


# --- load_overrides ---

def test_load_overrides_missing_file_is_empty_not_an_error(tmp_path):
    assert load_overrides(tmp_path / "nope.json") == {}


def test_load_overrides_skips_metadata_and_renormalizes_keys(tmp_path):
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({
        "_comment": ["ignored"],
        "_notes": {"x": "y"},
        "overrides": {"Gary Payton Sr.": 12345, "some typo'd name": 999},
    }), encoding="utf-8")
    got = load_overrides(path)
    assert got == {"garypayton": 12345, "sometypodname": 999}


def test_load_overrides_accepts_flat_mapping_without_wrapper(tmp_path):
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"_comment": "hi", "Foo Bar": 7}), encoding="utf-8")
    assert load_overrides(path) == {"foobar": 7}


def test_load_overrides_rejects_non_integer_id(tmp_path):
    path = tmp_path / "overrides.json"
    path.write_text(json.dumps({"overrides": {"Foo Bar": "2544"}}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_overrides(path)


def test_shipped_overrides_file_loads():
    # crosswalk/overrides.json is committed and must stay parseable even while empty.
    assert isinstance(load_overrides(), dict)


# --- collisions ---

def test_collisions_flags_only_keys_with_multiple_ids():
    key_map = {
        "garypayton": [{"id": 1, "name": "Gary Payton"}, {"id": 2, "name": "Gary Payton II"}],
        "lukadoncic": [{"id": 3, "name": "Luka Dončić"}],
    }
    found = collisions(key_map)
    assert set(found) == {"garypayton"}
    assert len(found["garypayton"]) == 2


# --- resolve ---

_KEY_MAP = {
    "lukadoncic": [{"id": 77, "name": "Luka Dončić"}],
    "garypayton": [{"id": 1, "name": "Gary Payton"}, {"id": 2, "name": "Gary Payton II"}],
}


def test_resolve_ok_single_hit():
    assert resolve("Luka Doncic", _KEY_MAP, overrides={}) == (77, "ok")


def test_resolve_miss_when_key_absent():
    assert resolve("Nobody Here", _KEY_MAP, overrides={}) == (None, "miss")


def test_resolve_collision_when_key_shared():
    assert resolve("Gary Payton Jr.", _KEY_MAP, overrides={}) == (None, "collision")


def test_resolve_override_wins_over_collision():
    overrides = {"garypayton": 2}
    assert resolve("Gary Payton", _KEY_MAP, overrides=overrides) == (2, "override")


def test_resolve_override_wins_over_miss():
    overrides = {"someguy": 555}
    assert resolve("Some Guy", _KEY_MAP, overrides=overrides) == (555, "override")
