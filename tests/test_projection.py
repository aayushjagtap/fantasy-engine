"""tests/test_projection.py -- moved from engine/projection.py's --selftest."""
from __future__ import annotations

import pytest

from engine.projection import (
    RETURN_GP_FACTOR_PER_MISSED_SEASON,
    _next_season_label,
    _weighted_line,
    age_multiplier,
    project_current_roster,
    project_players,
)


def test_age_multiplier_shape():
    assert age_multiplier(22, "ast") > 1.0          # young -> growth
    assert age_multiplier(34, "blk") < 1.0          # old + athletic -> decline
    assert age_multiplier(29, "scoring") == 1.0     # plateau between peak and decline
    assert age_multiplier(None, "scoring") == 1.0   # no age -> no adjustment


def _synthetic_histories():
    young = [
        dict(name="youngster", age=22, gp=70, min=32, fgm=8, fga=16, fg3m=2, ftm=4, fta=5,
             reb=6, ast=5, stl=1.4, blk=0.8, tov=2.0),
        dict(name="youngster", age=21, gp=68, min=30, fgm=7, fga=15, fg3m=1.6, ftm=3.5, fta=4.5,
             reb=5.5, ast=4.2, stl=1.2, blk=0.7, tov=1.9),
        dict(name="youngster", age=20, gp=60, min=26, fgm=6, fga=13, fg3m=1.2, ftm=3, fta=4,
             reb=5, ast=3.5, stl=1.0, blk=0.6, tov=1.8),
    ]
    vet = [
        dict(name="vet", age=34, gp=65, min=30, fgm=7, fga=15, fg3m=1.5, ftm=3, fta=3.5,
             reb=8, ast=4, stl=1.0, blk=1.6, tov=2.0),
        dict(name="vet", age=33, gp=70, min=32, fgm=7.5, fga=15.5, fg3m=1.6, ftm=3.2, fta=3.7,
             reb=8.5, ast=4.2, stl=1.1, blk=1.8, tov=2.1),
        dict(name="vet", age=32, gp=72, min=33, fgm=8, fga=16, fg3m=1.7, ftm=3.4, fta=3.9,
             reb=9, ast=4.5, stl=1.2, blk=2.0, tov=2.2),
    ]
    return young, vet


def test_project_players_role_trend_and_aging():
    young, vet = _synthetic_histories()
    seasons = [{1: young[0], 2: vet[0]}, {1: young[1], 2: vet[1]}, {1: young[2], 2: vet[2]}]
    projected, aged = project_players(seasons)
    assert aged == 2

    # Role trend: youngster's minutes rise (32<-30<-26) -> mult>1; vet falls -> mult<1.
    assert projected[1]["role_mult"] > 1.0, projected[1]["role_mult"]
    assert projected[2]["role_mult"] < 1.0, projected[2]["role_mult"]

    base_young = _weighted_line(young)
    base_vet = _weighted_line(vet)
    # Young player's assists projected ABOVE their weighted history (growth).
    assert projected[1]["ast"] > base_young["ast"]
    # Vet's blocks projected BELOW their weighted history (athletic decline).
    assert projected[2]["blk"] < base_vet["blk"]
    # Efficiency is preserved: projected FG% ~ the weighted-history FG%.
    hist_fg = base_young["fgm"] / base_young["fga"]
    assert abs(projected[1]["fg_pct"] - hist_fg) < 1e-9

    # Eyeball check on curve magnitude, visible with `pytest -s`.
    print("\nselftest ok: young player grows, vet's athletic stats fade, FG% preserved")
    print(f"  youngster proj age {projected[1]['age']}: ast {base_young['ast']:.2f} -> {projected[1]['ast']:.2f}")
    print(f"  vet proj age {projected[2]['age']}: blk {base_vet['blk']:.2f} -> {projected[2]['blk']:.2f}")


def test_next_season_label():
    assert _next_season_label("2025-26") == "2026-27"


def _returner_line(**overrides):
    line = dict(name="Returner", age=25, gp=60, min=30, fgm=8, fga=17, fg3m=2,
                ftm=4, fta=5, reb=5, ast=6, stl=1.2, blk=0.5, tov=2.5,
                position=("G",), team="OLD")
    line.update(overrides)
    return line


def test_current_roster_player_with_no_gap_keeps_existing_projection():
    seasons = [
        {1: _returner_line()},
        {1: _returner_line(age=24, min=28)},
        {1: _returner_line(age=23, min=26)},
    ]
    before, _ = project_players(seasons)
    current, _ = project_players(
        seasons, season_labels=["2025-26", "2024-25", "2023-24"],
        target_season="2026-27",
        current_roster={1: {"team": "NEW", "position": ("F",)}})
    for key in ("gp", "min", "fgm", "fga", "fg3m", "ftm", "fta", "reb", "ast", "stl", "blk", "tov", "pts", "fg_pct", "ft_pct"):
        assert current[1][key] == before[1][key]
    assert current[1]["team"] == "NEW"
    assert current[1]["position"] == ("F",)
    assert not current[1]["used_returner_fallback"]
    assert current[1]["expected_games_adjustment"] == 1.0


def test_current_roster_returner_uses_elapsed_age_and_games_adjustment():
    seasons = [
        {},
        {2: _returner_line(age=24, min=32, gp=70)},
        {2: _returner_line(age=23, min=28, gp=65)},
    ]
    projected, _ = project_players(
        seasons, season_labels=["2025-26", "2024-25", "2023-24"],
        target_season="2026-27",
        current_roster={2: {"team": "IND", "position": ("G", "F")}})
    line = projected[2]
    assert line["age"] == 26
    assert line["last_actual_season"] == "2024-25"
    assert line["missing_seasons"] == 1
    assert line["used_returner_fallback"] is True
    assert line["expected_games_adjustment"] == RETURN_GP_FACTOR_PER_MISSED_SEASON
    assert line["gp"] < 70
    assert line["team"] == "IND"
    assert line["position"] == ("G", "F")
    assert line["role_trend_mult"] == 1.0
    assert line["pts"] == 2 * line["fgm"] + line["fg3m"] + line["ftm"]
    assert line["fg_pct"] == line["fgm"] / line["fga"]
    assert line["ft_pct"] == line["ftm"] / line["fta"]


def test_current_roster_returner_with_excessively_old_history_is_excluded():
    projected, _ = project_players(
        [{}, {}, {}, {}, {}, {3: _returner_line(age=26)}],
        season_labels=["2025-26", "2024-25", "2023-24", "2022-23", "2021-22", "2020-21"],
        target_season="2026-27",
        current_roster={3: {"team": "PHI", "position": ("C",)}})
    assert 3 not in projected


def test_age_uses_season_labels_not_number_of_historical_appearances():
    projected, _ = project_players(
        [{}, {}, {8: _returner_line(age=23, min=32)}, {},
         {8: _returner_line(age=21, min=24)}, {8: _returner_line(age=20, min=20)}],
        season_labels=["2025-26", "2024-25", "2023-24", "2022-23", "2021-22", "2020-21"],
        target_season="2026-27",
        current_roster={8: {"team": "HOU", "position": ("G",)}})
    assert projected[8]["age"] == 26
    assert projected[8]["missing_seasons"] == 2
    assert projected[8]["expected_games_adjustment"] == RETURN_GP_FACTOR_PER_MISSED_SEASON ** 2
    assert projected[8]["role_trend_mult"] == 1.0


def test_current_roster_without_stats_and_fallback_only_players_are_excluded():
    seasons = [{4: _returner_line(name="Fallback only")}, {}, {}]
    projected, _ = project_players(
        seasons, season_labels=["2025-26", "2024-25", "2023-24"],
        target_season="2026-27",
        current_roster={5: {"name": "No history", "team": "WAS", "position": ("G",)}})
    assert projected == {}


def test_historical_projection_does_not_use_future_roster_membership():
    seasons = [{}, {6: _returner_line(age=24)}, {}]
    projected, _ = project_players(
        seasons, season_labels=["2024-25", "2023-24", "2022-23"],
        target_season="2025-26")
    assert 6 not in projected


def test_returner_with_missing_age_keeps_age_neutral_adjustment():
    projected, _ = project_players(
        [{}, {7: _returner_line(age=None)}],
        season_labels=["2025-26", "2024-25"], target_season="2026-27",
        current_roster={7: {"team": "DAL", "position": ()}})
    assert projected[7]["age"] is None
    assert projected[7]["gp"] > 0


def test_current_projection_loader_requires_complete_roster_and_uses_current_membership(monkeypatch):
    seasons = ["2025-26", "2024-25", "2023-24"]
    lines = {
        "2025-26": {10: _returner_line(name="Fallback only")},
        "2024-25": {11: _returner_line(name="Returning")},
        "2023-24": {11: _returner_line(name="Returning", age=24)},
    }
    roster = {11: {"name": "Returning", "team": "IND", "position": ("G",)}}
    rookie_targets = []
    override_targets = []
    monkeypatch.setattr("ingest.nba_boxscores.recent_completed_seasons", lambda n: seasons[:n])
    monkeypatch.setattr("ingest.nba_boxscores.get_season_boxscores",
                        lambda season, offline=False: (lines[season], "cache"))
    monkeypatch.setattr("ingest.nba_rosters.get_season_rosters",
                        lambda season, offline=False: (roster, {
                            "teams_total": 2, "teams_with_data": 2,
                        }))
    monkeypatch.setattr("engine.projection.load_rookies",
                        lambda target_season=None: rookie_targets.append(target_season) or {})
    monkeypatch.setattr("engine.projection.load_role_overrides",
                        lambda target_season=None: override_targets.append(target_season) or {})

    projected, _aged, target = project_current_roster(n_seasons=3)
    assert target == "2026-27"
    assert set(projected) == {11}
    assert projected[11]["team"] == "IND"
    assert rookie_targets == override_targets == ["2026-27"]

    monkeypatch.setattr("ingest.nba_rosters.get_season_rosters",
                        lambda season, offline=False: (roster, {
                            "teams_total": 2, "teams_with_data": 1,
                        }))
    with pytest.raises(ValueError, match="complete 2026-27 roster cache required"):
        project_current_roster(n_seasons=3)
