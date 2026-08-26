"""tests/test_role_overrides.py -- C4: role overrides + team-change diagnostic, offline."""
from __future__ import annotations

import json

from engine.league_config import standard_9cat
from engine.projection import ROLE_OVERRIDES_PATH, load_role_overrides, project_players
from engine.value import compute_values
from ingest.nba_rosters import team_changes


def _rising_minutes_history():
    """Returning player id 7, minutes rising 26 -> 30 -> 34 (newest first) so
    _role_trend_mult returns > 1 -- an override to < 1 is then unambiguous."""
    s1 = dict(name="p", age=25, gp=70, min=34, fgm=8, fga=16, fg3m=2, ftm=4, fta=5,
              reb=6, ast=5, stl=1.2, blk=0.6, tov=2.0)
    s2 = dict(s1, age=24, min=30, gp=68)
    s3 = dict(s1, age=23, min=26, gp=60)
    return [{7: s1}, {7: s2}, {7: s3}]


# --- load_role_overrides ---

def test_load_role_overrides_parses_and_ignores_underscore_keys(tmp_path):
    p = tmp_path / "ro.json"
    p.write_text(json.dumps({"_comment": "x", "_notes": {"1": "y"}, "2544": 1.1, "203507": 0.9}))
    assert load_role_overrides(str(p)) == {2544: 1.1, 203507: 0.9}


def test_load_role_overrides_missing_file_returns_empty(tmp_path):
    assert load_role_overrides(str(tmp_path / "nope.json")) == {}


# --- application in project_players ---

def test_override_replaces_trend_and_pre_override_value_is_recorded():
    seasons = _rising_minutes_history()
    base, _ = project_players(seasons)
    trend = base[7]["role_mult"]
    assert trend > 1.0                              # sanity: rising minutes
    assert base[7]["role_trend_mult"] == trend      # always recorded
    assert "role_override" not in base[7]

    ovr, _ = project_players(seasons, role_overrides={7: 0.80})
    assert ovr[7]["role_mult"] == 0.80              # override IS the effective mult
    assert ovr[7]["role_override"] == 0.80
    assert ovr[7]["role_trend_mult"] == trend       # model value still visible
    # volume scaled by the override, not the trend: compare against a role-neutral
    # projection (role_damp=0 -> trend mult 1.0) so the factor is exactly 0.80.
    neutral, _ = project_players(seasons, role_damp=0.0)
    assert abs(ovr[7]["ast"] - neutral[7]["ast"] * 0.80) < 1e-9
    assert abs(ovr[7]["ast"] / base[7]["ast"] - 0.80 / trend) < 1e-2  # ballpark vs trended


def test_no_override_leaves_role_mult_equal_to_trend():
    seasons = _rising_minutes_history()
    proj, _ = project_players(seasons, role_overrides={999: 1.5})  # unrelated id
    assert proj[7]["role_mult"] == proj[7]["role_trend_mult"]
    assert "role_override" not in proj[7]


def test_explain_prints_override_and_pre_override_value(capsys):
    seasons = _rising_minutes_history()
    proj, _ = project_players(seasons, role_overrides={7: 0.80})
    from engine.diagnose import explain
    explain(proj, standard_9cat(), "p", min_gp=1, pool_size=1)
    out = capsys.readouterr().out
    assert "MANUAL OVERRIDE" in out
    assert "0.800" in out          # effective
    assert "trajectory" in out     # names the pre-override source


# --- the shipped file ---

def test_shipped_role_overrides_file_parses():
    ro = load_role_overrides(ROLE_OVERRIDES_PATH)
    assert isinstance(ro, dict)
    for k, v in ro.items():
        assert isinstance(k, int)
        assert isinstance(v, float) and v > 0


# --- team-change diagnostic (reads the committed CommonTeamRoster cache) ---

def test_team_changes_offline_returns_differing_team_pairs():
    changes = team_changes(offline=True)
    assert isinstance(changes, dict) and changes  # cache has both seasons, 30/30 teams
    for pid, pair in changes.items():
        assert isinstance(pid, int)
        old, new = pair
        assert old and new and old != new
