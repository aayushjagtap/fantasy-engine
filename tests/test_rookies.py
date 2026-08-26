"""tests/test_rookies.py -- C3: draft-class rookie merge, fully offline."""
from __future__ import annotations

import json

from engine.league_config import standard_9cat
from engine.projection import ROOKIES_PATH, load_rookies, project_players
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


def _full_entry(nid=-2026001, **over):
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
    assert set(rk) == {-2026001}
    line = rk[-2026001]
    assert line["is_rookie"] is True
    assert line["role_mult"] == 1.0
    assert line["position"] == ("F",)
    assert line["team"] == "WAS"
    assert line["pts"] == 2 * 6.0 + 1.5 + 3.0
    assert abs(line["fg_pct"] - 6.0 / 14.0) < 1e-9
    assert abs(line["ft_pct"] - 3.0 / 4.0) < 1e-9


def test_load_rookies_skips_incomplete_stat_lines_and_logs_count(tmp_path, capsys):
    good = _full_entry(nid=-2026001)
    bad = _full_entry(nid=-2026002, min=None, fgm=None)
    rk = load_rookies(_write_file(tmp_path, [good, bad]))
    assert set(rk) == {-2026001}
    assert "1 skipped" in capsys.readouterr().err


def test_load_rookies_missing_file_returns_empty(tmp_path):
    assert load_rookies(str(tmp_path / "nope.json")) == {}


# --- project_players merge ---

def test_project_players_merges_rookies_without_touching_returners():
    seasons = _returning_history()
    with_rk, _ = project_players(seasons, rookies={-2026001: _rookie_line()})
    without_rk, _ = project_players(seasons)
    assert with_rk[-2026001]["is_rookie"] is True
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
    proj, _ = project_players(seasons, rookies={-2026001: _rookie_line()})
    board = compute_values(proj, standard_9cat(), min_gp=1, pool_size=2)
    assert next(r for r in board if r["nba_id"] == -2026001)["is_rookie"] is True
    assert next(r for r in board if r["nba_id"] == 1)["is_rookie"] is False


# --- the shipped file ---

def test_shipped_rookies_file_is_well_formed():
    with open(ROOKIES_PATH, "r", encoding="utf-8") as f:
        doc = json.load(f)
    assert doc.get("season") == "2026-27"
    seen = set()
    for e in doc["rookies"]:
        assert isinstance(e["nba_id"], int) and e["nba_id"] < 0, e   # synthetic negative
        assert e["nba_id"] not in seen, f"duplicate id {e['nba_id']}"
        seen.add(e["nba_id"])
        assert e.get("name")
        for group in (e.get("position") or []):
            assert group in ("G", "F", "C"), e
        for key in ("gp", "min", "fgm", "fga", "fg3m", "ftm", "fta", "reb", "ast", "stl", "blk", "tov"):
            assert key in e, f"{e['name']} missing stat key {key}"


def test_loader_tolerates_the_shipped_null_stat_file():
    # Ships with null stat lines on purpose -> loader returns a dict, skipping all.
    assert isinstance(load_rookies(ROOKIES_PATH, verbose=False), dict)
