"""tests/test_league_config.py -- B1: config as data, not code.

Covers LeagueConfig.load/save/to_json, the leagues/ worked examples, the
enum-keyed dict round trip, and the validators still firing through
model_validate (not just direct construction).
"""
from __future__ import annotations

import os

import pydantic
import pytest

from engine.league_config import LeagueConfig, ScoringType, StatCategory, punt_ft_9cat, standard_9cat
from engine.value import compute_values
from tests.fixtures import SIX_PLAYER_FIXTURE

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LEAGUES_DIR = os.path.join(_ROOT, "leagues")
LEAGUE_FILES = sorted(
    os.path.join(_LEAGUES_DIR, f) for f in os.listdir(_LEAGUES_DIR) if f.endswith(".json")
)


@pytest.mark.parametrize("path", LEAGUE_FILES, ids=[os.path.basename(p) for p in LEAGUE_FILES])
def test_league_file_loads_and_validates(path):
    cfg = LeagueConfig.load(path)
    assert isinstance(cfg, LeagueConfig)


def test_standard_9cat_json_matches_constructor():
    loaded = LeagueConfig.load(os.path.join(_LEAGUES_DIR, "standard_9cat.json"))
    assert loaded == standard_9cat()


def test_standard_9cat_json_produces_identical_board():
    # Config-object equality isn't the same as engine-behavior equality --
    # this is the behavioral guarantee B1 actually promises.
    loaded = LeagueConfig.load(os.path.join(_LEAGUES_DIR, "standard_9cat.json"))
    from_file = compute_values(SIX_PLAYER_FIXTURE, loaded, min_gp=1, pool_size=6)
    from_ctor = compute_values(SIX_PLAYER_FIXTURE, standard_9cat(), min_gp=1, pool_size=6)
    assert from_file == from_ctor


def test_round_trip_json_preserves_equality(tmp_path):
    cfg = punt_ft_9cat()
    path = tmp_path / "roundtrip.json"
    cfg.to_json(path)
    loaded = LeagueConfig.load(path)
    assert loaded == cfg


def test_round_trip_via_save(tmp_path):
    cfg = punt_ft_9cat()
    path = tmp_path / "roundtrip_save.json"
    cfg.save(path)
    loaded = LeagueConfig.load(path)
    assert loaded == cfg


def test_enum_keyed_categories_round_trip(tmp_path):
    cfg = standard_9cat()
    path = tmp_path / "enum_roundtrip.json"
    cfg.to_json(path)
    loaded = LeagueConfig.load(path)
    assert StatCategory.FG_PCT in loaded.categories
    assert isinstance(next(iter(loaded.categories)), StatCategory)


def test_punt_config_excludes_punted_category_from_active():
    cfg = LeagueConfig.load(os.path.join(_LEAGUES_DIR, "punt_ft.json"))
    assert StatCategory.FT_PCT not in cfg.active_categories
    assert StatCategory.FT_PCT in cfg.punted_categories


def test_load_missing_file_raises_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError) as exc_info:
        LeagueConfig.load(tmp_path / "does_not_exist.json")
    assert "leagues/" in str(exc_info.value)


def test_load_unsupported_extension_raises():
    with pytest.raises(ValueError):
        LeagueConfig.load("leagues/standard_9cat.txt")


def test_category_league_rejects_point_values():
    with pytest.raises(pydantic.ValidationError):
        LeagueConfig.model_validate({
            "scoring_type": "category",
            "categories": {"pts": {}},
            "point_values": {"pts": 1.0},
        })


def test_points_league_rejects_empty_point_values():
    with pytest.raises(pydantic.ValidationError):
        LeagueConfig.model_validate({
            "scoring_type": "points",
            "point_values": {},
        })


def test_unknown_category_name_rejects():
    with pytest.raises(pydantic.ValidationError):
        LeagueConfig.model_validate({
            "scoring_type": "category",
            "categories": {"made_up_cat": {}},
        })
