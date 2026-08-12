"""tests/test_projection.py -- moved from engine/projection.py's --selftest."""
from __future__ import annotations

from engine.projection import _next_season_label, _weighted_line, age_multiplier, project_players


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
