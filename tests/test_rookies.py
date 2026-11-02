"""tests/test_rookies.py -- C3: draft-class rookie merge, fully offline."""
from __future__ import annotations

import json
import unicodedata

from engine.league_config import standard_9cat
from engine.projection import ROOKIES_PATH, load_rookies, project_players
from engine.rookie_baseline import (load_comparator_baselines, make_rookie_line,
                                    primary_group, summarize_comparators)
from engine.value import compute_values


# --- synthetic fixtures ---

def _returning_history():
    """One returning player (id 1), two seasons newest-first -- enough for
    project_players to build a real projection to protect against clobbering."""
    a = dict(name="vet", age=27, gp=70, min=32, fgm=8, fga=16, fg3m=2, ftm=4, fta=5,
             reb=6, ast=5, stl=1.2, blk=0.6, tov=2.0)
    b = dict(name="vet", age=26, gp=68, min=31, fgm=7.6, fga=15.4, fg3m=1.8, ftm=3.8, fta=4.7,
             reb=5.7, ast=4.6, stl=1.1, blk=0.6, tov=1.9)
    return [{1: a}, {1: b}]


def _full_entry(nid=1643407, **over):
    e = dict(nba_id=nid, name="Test Rookie", team="WAS", draft_pick=1, position=["F"], age=19,
             gp=65, min=28.0, fgm=6.0, fga=14.0, fg3m=1.5, ftm=3.0, fta=4.0,
             reb=5.0, ast=3.0, stl=1.0, blk=0.6, tov=2.2)
    e.update(over)
    return e


def _write_file(tmp_path, entries):
    p = tmp_path / "rookies.json"
    p.write_text(json.dumps({"season": "2026-27", "rookies": entries}))
    return str(p)


def _rookie_line(**over):
    """A ready-made projected line (what load_rookies would emit) for merge tests."""
    line = dict(name="R", age=19, gp=65, min=28.0, fgm=6.0, fga=14.0, fg3m=1.5,
                ftm=3.0, fta=4.0, reb=5.0, ast=3.0, stl=1.0, blk=0.6, tov=2.2,
                pts=16.5, fg_pct=6.0 / 14.0, ft_pct=0.75,
                position=("F",), team="WAS", role_mult=1.0, is_rookie=True)
    line.update(over)
    return line


# --- load_rookies ---

def test_load_rookies_derives_points_percentages_and_flags(tmp_path):
    rk = load_rookies(_write_file(tmp_path, [_full_entry()]))
    assert set(rk) == {1643407}
    line = rk[1643407]
    assert line["is_rookie"] is True
    assert line["role_mult"] == 1.0
    assert line["position"] == ("F",)
    assert line["team"] == "WAS"
    assert line["pts"] == 2 * 6.0 + 1.5 + 3.0
    assert abs(line["fg_pct"] - 6.0 / 14.0) < 1e-9
    assert abs(line["ft_pct"] - 3.0 / 4.0) < 1e-9


def test_load_rookies_skips_incomplete_stat_lines_and_logs_count(tmp_path, capsys):
    good = _full_entry(nid=1643407)
    bad = _full_entry(nid=1643408, min=None, fgm=None)
    rk = load_rookies(_write_file(tmp_path, [good, bad]))
    assert set(rk) == {1643407}
    assert "1 skipped" in capsys.readouterr().err


def test_load_rookies_missing_file_returns_empty(tmp_path):
    assert load_rookies(str(tmp_path / "nope.json")) == {}


def test_load_rookies_only_applies_to_matching_target_season(tmp_path):
    path = _write_file(tmp_path, [_full_entry()])
    assert load_rookies(path, verbose=False, target_season="2025-26") == {}
    assert set(load_rookies(path, verbose=False, target_season="2026-27")) == {1643407}


# --- project_players merge ---

def test_project_players_merges_rookies_without_touching_returners():
    seasons = _returning_history()
    with_rk, _ = project_players(seasons, rookies={1643407: _rookie_line()})
    without_rk, _ = project_players(seasons)
    assert with_rk[1643407]["is_rookie"] is True
    assert with_rk[1] == without_rk[1]  # returning player's projection unchanged


def test_project_players_rookie_never_clobbers_a_real_projection():
    seasons = _returning_history()
    # a hand line claiming id 1 (a real returning player) must lose to setdefault
    proj, _ = project_players(seasons, rookies={1: _rookie_line(name="imposter")})
    assert proj[1]["name"] == "vet"
    assert not proj[1].get("is_rookie")


def test_project_players_no_rookies_arg_is_unchanged_behaviour():
    seasons = _returning_history()
    a, _ = project_players(seasons)
    b, _ = project_players(seasons, rookies={})
    assert a == b


# --- board integration ---

def test_compute_values_row_carries_is_rookie_flag():
    seasons = _returning_history()
    proj, _ = project_players(seasons, rookies={1643407: _rookie_line()})
    board = compute_values(proj, standard_9cat(), min_gp=1, pool_size=2)
    assert next(r for r in board if r["nba_id"] == 1643407)["is_rookie"] is True
    assert next(r for r in board if r["nba_id"] == 1)["is_rookie"] is False


# --- the shipped file ---

def test_shipped_rookies_file_is_well_formed():
    with open(ROOKIES_PATH, "r", encoding="utf-8") as f:
        doc = json.load(f)
    assert doc.get("season") == "2026-27"
    seen = set()
    for e in doc["rookies"]:
        assert isinstance(e["nba_id"], int) and e["nba_id"] > 0, e
        assert e["nba_id"] not in seen, f"duplicate id {e['nba_id']}"
        seen.add(e["nba_id"])
        assert e.get("name")
        for group in (e.get("position") or []):
            assert group in ("G", "F", "C"), e
        for key in ("gp", "min", "fgm", "fga", "fg3m", "ftm", "fta", "reb", "ast", "stl", "blk", "tov"):
            assert key in e, f"{e['name']} missing stat key {key}"
    assert [e["draft_pick"] for e in doc["rookies"]] == list(range(1, 31))
    assert len(doc["rookies"]) == 30
    assert [e["name"] for e in doc["rookies"]] == [
        "AJ Dybantsa", "Darryn Peterson", "Cameron Boozer", "Caleb Wilson", "Keaton Wagler",
        "Mikel Brown Jr.", "Darius Acuff Jr.", "Kingston Flemings", "Morez Johnson Jr.",
        "Brayden Burries", "Yaxel Lendeborg", "Aday Mara", "Nate Ament", "Hannes Steinbach",
        "Dailyn Swain", "Bennett Stirtz", "Ebuka Okorie", "Christian Anderson", "Allen Graves",
        "Jayden Quaintance", "Karim L\u00f3pez", "Labaron Philon Jr.", "Zuby Ejiofor", "Cameron Carr",
        "Sergio de Larrea", "Tarris Reed Jr.", "Chris Cenac Jr.", "Joshua Jefferson", "Alex Karaban",
        "Koa Peat",
    ]
    assert [e["nba_id"] for e in doc["rookies"]] == [
        1643407, 1643408, 1643409, 1643410, 1643413, 1643414, 1643411, 1643412, 1643516,
        1643415, 1642865, 1643530, 1643417, 1643419, 1643517, 1642892, 1643536, 1643515,
        1643512, 1643519, 1643510, 1642889, 1643544, 1643418, 1643547, 1643542, 1643416,
        1643538, 1642284, 1643520,
    ]


def test_shipped_ids_are_real_and_unique():
    with open(ROOKIES_PATH, encoding="utf-8") as f:
        entries = json.load(f)["rookies"]
    ids = [e["nba_id"] for e in entries]
    assert all(pid > 0 for pid in ids)
    assert len(ids) == len(set(ids))


def test_shipped_rookie_rosters_match_committed_cache():
    from ingest.nba_rosters import get_season_rosters
    with open(ROOKIES_PATH, encoding="utf-8") as f:
        entries = json.load(f)["rookies"]
    roster, coverage = get_season_rosters("2026-27", offline=True)
    assert coverage["teams_with_data"] == coverage["teams_total"] == 30
    assert all(e["nba_id"] in roster for e in entries)
    for entry in entries:
        cached = roster[entry["nba_id"]]
        assert entry["team"] == cached["team"]
        assert tuple(entry["position"]) == tuple(cached["position"])
        # The cache contains U+FFFD in Karim López's spelling; the authoritative
        # NBA.com draft result spelling is retained in the shipped file.
        norm = lambda s: "".join(c for c in unicodedata.normalize("NFKD", s.replace("\ufffd", "o").casefold())
                                  if not unicodedata.combining(c) and c.isalnum())
        assert norm(entry["name"]) == norm(cached["name"])


def test_comparator_filters_and_medians_are_deterministic():
    row = dict(age=19, gp=50, min=20, fgm=4, fga=9, fg3m=1, ftm=2, fta=3,
               reb=4, ast=2, stl=1, blk=.5, tov=1.5)
    seasons = {"2023-24": {1: row, 2: dict(row, age=21), 3: dict(row, gp=39)},
               "2024-25": {1: dict(row, min=30)}}
    positions = {pid: ("G",) for pid in (1, 2, 3)}
    sparse = summarize_comparators(seasons, positions)
    assert sparse["G"]["sample_size"] == 2  # one player's seasons are separate observations
    assert sparse["G"]["components"] is None
    assert sparse["G"]["supported"] is False

    populated = {"2024-25": {pid: dict(row, min=10 + pid) for pid in range(1, 21)}}
    populated_positions = {pid: ("G",) for pid in range(1, 21)}
    a = summarize_comparators(populated, populated_positions)
    b = summarize_comparators(populated, populated_positions)
    assert a == b
    assert a["G"]["sample_size"] == 20
    assert a["G"]["components"]["min"] == 20.5
    assert a["G"]["supported"] is True


def test_multi_position_policy_is_fixed_and_unsupported_center_stays_incomplete():
    assert primary_group(("F", "G")) == "F"
    assert primary_group(("C", "F")) == "C"
    assert make_rookie_line({"name": "Center", "position": ["C", "F"], "baseline_position": "C",
                             "team": "X", "draft_pick": 1}, {"supported": False}) is None


def test_baseline_components_and_manual_multiplier_preserve_consistency():
    entry = dict(name="Darryn Peterson", age=None, position=["G"], baseline_position="G",
                 team="UTA", draft_pick=2)
    baseline = {"supported": True, "sample_size": 24, "criteria": "fixture", "components": {
        "gp": 60, "min": 20, "fgm": 4, "fga": 9, "fg3m": 1, "ftm": 2, "fta": 3,
        "reb": 4, "ast": 2, "stl": 1, "blk": .5, "tov": 1.5}}
    line = make_rookie_line(entry, baseline)
    assert line["gp"] == 60
    assert line["min"] == 26
    assert abs(line["fga"] - 11.7) < 1e-9
    assert line["pts"] == 2 * line["fgm"] + line["fg3m"] + line["ftm"]
    assert line["fg_pct"] == line["fgm"] / line["fga"]
    assert line["ft_pct"] == line["ftm"] / line["fta"]
    assert line["projection_is_estimate"] is True
    assert line["comparator_sample_size"] == 24


def test_rookies_are_target_season_scoped_when_merging():
    line = _rookie_line(rookie_season="2026-27")
    projected, _ = project_players(_returning_history(), rookies={1643407: line},
                                   target_season="2025-26")
    assert 1643407 not in projected


def test_rookie_merge_still_requires_current_roster_membership():
    rookie = _rookie_line(rookie_season="2026-27")
    projected, _ = project_players(
        _returning_history(), rookies={1643407: rookie},
        season_labels=["2025-26", "2024-25"], target_season="2026-27",
        current_roster={1: {"name": "vet", "team": "WAS", "position": ("G",)}})
    assert 1643407 not in projected


def test_committed_comparator_groups_enforce_minimum_sample():
    baselines, meta = load_comparator_baselines()
    assert meta["seasons"] == ["2020-21", "2021-22", "2022-23", "2023-24", "2024-25", "2025-26"]
    assert baselines["G"]["sample_size"] >= 20
    assert baselines["F"]["sample_size"] >= 20
    assert baselines["C"]["sample_size"] < 20
    shipped = load_rookies(ROOKIES_PATH, verbose=False, target_season="2026-27")
    assert all(line["projection_source"] == "historical_young_player_baseline" for line in shipped.values())
    assert len(shipped) == 28
    assert 1643530 not in shipped  # Aday Mara, unsupported C baseline
    assert 1643542 not in shipped  # Tarris Reed Jr., unsupported C baseline
