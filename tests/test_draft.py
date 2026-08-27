"""tests/test_draft.py -- E2, the draft assistant engine half (engine/draft.py).

Offline, synthetic fixtures only. Covers:
  * roster-slot legality: filled position slots block an eligible candidate,
    multi-position (F/G) eligibility is honoured, an unknown position is
    UTIL/BENCH-only (not a wildcard), and missing position data disables the gate
  * the blend: "vor" reproduces the VOR order, "marginal" reproduces the
    marginal_value order, and blend_weight follows k / starter_slots
  * positional scarcity is reported always but only re-ranks when
    scarcity_lambda > 0
  * the explanation payload: per-category deltas sum to the total, slot_fit is
    populated, and inferred punts are marked respected / pushed
  * recommend() drops roster members and (by default) illegal candidates
  * engine/draft.py imports nothing from cli/
"""
from __future__ import annotations

import pathlib
import re

import pytest

from engine.draft import (
    _starter_slot_count, blend_weight, legal_additions, positional_pressure,
    recommend, slot_fit,
)
from engine.league_config import (
    CategorySetting, LeagueConfig, RosterSlot, ScoringType, StatCategory,
)
from engine.team import Team

SC = StatCategory
_NINE = (SC.PTS, SC.REB, SC.AST, SC.STL, SC.BLK, SC.FG3M, SC.TOV, SC.FG_PCT, SC.FT_PCT)


def _p(name, pos, *, pts=12.0, reb=5.0, ast=3.0, stl=1.0, blk=0.6, fg3m=1.2,
       tov=1.6, fg_pct=0.47, fga=11.0, ft_pct=0.78, fta=3.0, gp=70):
    return dict(
        name=name, position=tuple(pos), gp=gp, min=30.0,
        pts=pts, reb=reb, ast=ast, stl=stl, blk=blk, fg3m=fg3m, tov=tov,
        fg_pct=fg_pct, fga=fga, fgm=round(fg_pct * fga, 3),
        ft_pct=ft_pct, fta=fta, ftm=round(ft_pct * fta, 3),
    )


# A pool with clear positional identities. ids are stable handles.
G1, G2, G3, G4, G5, G6, G7, G8 = 1, 2, 3, 4, 5, 6, 7, 8
F1, F2, F3, FG1, FG2 = 9, 10, 11, 12, 13
C1, C2, C3, C4, C5 = 14, 15, 16, 17, 18
X1 = 19                                   # no position on file

POOL = {
    G1: _p("g1", "G", pts=24, ast=8, stl=1.9, fg3m=2.8, reb=3.5, blk=0.2, ft_pct=0.88, fta=5),
    G2: _p("g2", "G", pts=21, ast=7, stl=1.7, fg3m=2.6, reb=3.2, blk=0.2, ft_pct=0.87, fta=4),
    G3: _p("g3", "G", pts=19, ast=6, stl=1.6, fg3m=2.4, reb=3.0, blk=0.2, ft_pct=0.85, fta=4),
    G4: _p("g4", "G", pts=17, ast=5.5, stl=1.5, fg3m=2.2, reb=2.8, blk=0.1, ft_pct=0.84, fta=3),
    G5: _p("g5", "G", pts=15, ast=5, stl=1.4, fg3m=2.0, reb=2.6, blk=0.1, ft_pct=0.83, fta=3),
    G6: _p("g6", "G", pts=13, ast=4.5, stl=1.3, fg3m=1.8, reb=2.4, blk=0.1, ft_pct=0.82, fta=2.5),
    G7: _p("g7", "G", pts=11, ast=4, stl=1.2, fg3m=1.6, reb=2.2, blk=0.1, ft_pct=0.81, fta=2),
    G8: _p("g8", "G", pts=9, ast=3.5, stl=1.1, fg3m=1.4, reb=2.0, blk=0.1, ft_pct=0.80, fta=2),
    F1: _p("f1", "F", pts=18, reb=8, ast=3, stl=1.1, blk=0.8, fg3m=1.5),
    F2: _p("f2", "F", pts=15, reb=7, ast=2.5, stl=1.0, blk=0.7, fg3m=1.2),
    F3: _p("f3", "F", pts=12, reb=6, ast=2.0, stl=0.9, blk=0.6, fg3m=1.0),
    FG1: _p("fg1", ("F", "G"), pts=20, reb=6, ast=5, stl=1.5, blk=0.5, fg3m=2.0),
    FG2: _p("fg2", ("F", "G"), pts=14, reb=5, ast=4, stl=1.2, blk=0.4, fg3m=1.6),
    C1: _p("c1", "C", pts=20, reb=12, ast=2, stl=0.7, blk=2.6, fg3m=0.1, fg_pct=0.58, fga=13, ft_pct=0.62, fta=6),
    C2: _p("c2", "C", pts=17, reb=11, ast=1.8, stl=0.6, blk=2.3, fg3m=0.1, fg_pct=0.60, fga=11, ft_pct=0.58, fta=6),
    C3: _p("c3", "C", pts=15, reb=10, ast=1.6, stl=0.6, blk=2.0, fg3m=0.1, fg_pct=0.57, fga=10, ft_pct=0.60, fta=5),
    C4: _p("c4", ("C", "F"), pts=13, reb=9, ast=1.5, stl=0.7, blk=1.7, fg3m=0.2, fg_pct=0.56, fga=9, ft_pct=0.64, fta=4),
    C5: _p("c5", "C", pts=11, reb=8, ast=1.3, stl=0.5, blk=1.5, fg3m=0.1, fg_pct=0.55, fga=8, ft_pct=0.61, fta=4),
    X1: _p("x1", (), pts=14, reb=6, ast=4, stl=1.1, blk=0.5, fg3m=1.4),
}


def _cfg(slots, *, num_teams=3, cats=_NINE):
    return LeagueConfig(
        name="draft-test", scoring_type=ScoringType.CATEGORY, num_teams=num_teams,
        categories={c: CategorySetting() for c in cats},
        roster=[RosterSlot(position=p, count=n) for p, n in slots],
    )


def _team(cfg, roster_ids):
    return Team(cfg, POOL, roster_ids, min_gp=1)


# --------------------------------------------------------------------------- #
# 1. roster-slot legality
# --------------------------------------------------------------------------- #

def test_legal_additions_blocks_when_position_slots_are_full():
    cfg = _cfg([("PG", 1), ("SG", 1), ("C", 1)])         # no UTIL/BENCH escape hatch
    team = _team(cfg, [G1, G2])                          # both guard slots effectively spoken for
    legal = legal_additions(team, POOL, [G3, C1])
    assert G3 not in legal                              # third guard-only player: nowhere to sit
    assert C1 in legal                                  # center still fits the open C slot


def test_multi_position_eligibility_is_honoured():
    cfg = _cfg([("PG", 1), ("SG", 1), ("C", 1)])
    team = _team(cfg, [G1, C1])                         # PG + C filled; SG open
    legal = legal_additions(team, POOL, [FG1, F1])
    assert FG1 in legal                                 # F/G player can take the SG slot
    assert F1 not in legal                              # F-only player fits no open slot


def test_unknown_position_is_not_a_wildcard():
    cfg = _cfg([("PG", 1), ("SG", 1), ("C", 1)])         # no position-blind slot
    team = _team(cfg, [G1, C1])                         # SG is the only opening
    legal = legal_additions(team, POOL, [X1, G2])
    assert X1 not in legal                              # () cannot fill a dedicated SG slot
    assert G2 in legal


def test_unknown_position_can_take_a_blind_slot():
    cfg = _cfg([("PG", 1), ("SG", 1), ("UTIL", 1)])
    team = _team(cfg, [G1, G2])                         # UTIL open
    assert X1 in legal_additions(team, POOL, [X1])


def test_missing_position_data_disables_the_gate():
    blind_pool = {i: {**p, "position": ()} for i, p in POOL.items()}
    cfg = _cfg([("PG", 1), ("SG", 1), ("C", 1)])
    team = Team(cfg, blind_pool, [G1, G2], min_gp=1)
    legal = legal_additions(team, blind_pool, [G3, C1, F1])
    assert legal == {G3, C1, F1}                        # nothing to gate on -> all legal


def test_slot_fit_reports_group_level_labels():
    cfg = _cfg([("PG", 1), ("SG", 1), ("SF", 1), ("PF", 1), ("C", 1), ("UTIL", 1), ("BENCH", 1)])
    team = _team(cfg, [])
    assert slot_fit(team, POOL, G1) == ("PG", "SG", "UTIL", "BENCH")      # a guard
    assert slot_fit(team, POOL, C1) == ("C", "UTIL", "BENCH")
    assert set(slot_fit(team, POOL, FG1)) == {"PG", "SG", "SF", "PF", "UTIL", "BENCH"}
    assert slot_fit(team, POOL, X1) == ("UTIL", "BENCH")                  # () -> blind only


# --------------------------------------------------------------------------- #
# 2. the blend
# --------------------------------------------------------------------------- #

def test_blend_weight_follows_roster_size_over_starter_slots():
    cfg = _cfg([("PG", 1), ("SG", 1), ("SF", 1), ("PF", 1), ("C", 1),
                ("UTIL", 2), ("BENCH", 3)])              # 7 starters, 10 total
    assert _starter_slot_count(cfg) == 7
    assert blend_weight(_team(cfg, []), "auto") == 0.0
    assert blend_weight(_team(cfg, [G1]), "auto") == pytest.approx(1 / 7)
    assert blend_weight(_team(cfg, [G1, G2, C1]), "auto") == pytest.approx(3 / 7)
    full = _team(cfg, [G1, G2, G3, G4, F1, C1, C2])
    assert blend_weight(full, "auto") == 1.0             # k == starters
    over = _team(cfg, list(POOL)[:11])
    assert blend_weight(over, "auto") == 1.0             # clamped past starters
    assert blend_weight(full, "vor") == 0.0
    assert blend_weight(_team(cfg, []), "marginal") == 1.0
    assert blend_weight(_team(cfg, []), 0.4) == pytest.approx(0.4)


def _rank_ids(rows):
    return [r["nba_id"] for r in rows]


def test_blend_vor_endpoint_matches_the_vor_board():
    from engine.value import compute_values

    cfg = _cfg([("PG", 1), ("SG", 1), ("SF", 1), ("C", 1), ("UTIL", 1), ("BENCH", 2)])
    team = _team(cfg, [G1, C1])
    avail = [G2, G3, G4, F1, F2, FG1, C2, C3]            # all legal on this roster
    recs = recommend(team, POOL, avail, n=99, blend="vor")
    board = compute_values({i: POOL[i] for i in avail}, cfg, min_gp=1)
    assert _rank_ids(recs) == [r["nba_id"] for r in board]


def test_blend_marginal_endpoint_matches_marginal_value_order():
    cfg = _cfg([("PG", 1), ("SG", 1), ("SF", 1), ("C", 1), ("UTIL", 1), ("BENCH", 2)])
    team = _team(cfg, [G1, C1])
    avail = [G2, G3, G4, F1, F2, FG1, C2, C3]
    recs = recommend(team, POOL, avail, n=99, blend="marginal")
    by_mv = sorted(avail, key=lambda c: team.marginal_value(c)["delta_expected_wins"],
                   reverse=True)
    assert _rank_ids(recs) == by_mv


# --------------------------------------------------------------------------- #
# 3. positional scarcity: reported always, applied only on opt-in
# --------------------------------------------------------------------------- #

def test_positional_pressure_flags_the_thin_position():
    cfg = _cfg([("PG", 1), ("C", 2)], num_teams=3)       # demand: G 3, C 6
    team = _team(cfg, [])
    avail = [G2, G3, G4, G5, G6, G7, G8, C2, C3]         # 7 guards, 2 centers left
    pres = positional_pressure(team, POOL, avail)
    assert pres["C"] > pres["G"]
    assert pres["C"] > 1.0                               # 6 demand / 2 supply


def test_scarcity_lambda_zero_is_default_and_a_high_lambda_lifts_centers():
    cfg = _cfg([("PG", 1), ("C", 2), ("UTIL", 1), ("BENCH", 2)], num_teams=3)
    team = _team(cfg, [G1])
    avail = [G2, G3, G4, G5, G6, F1, F2, C2, C3, C4]
    base = recommend(team, POOL, avail, n=99)                       # lambda defaults to 0.0
    lifted = recommend(team, POOL, avail, n=99, scarcity_lambda=8.0)
    assert all(r["scarcity"] >= 0.0 for r in base)
    assert [r["nba_id"] for r in base if r["scarcity"] == 0.0] != []  # some positions get no bonus

    def top_center_index(rows):
        return next(i for i, r in enumerate(rows) if r["nba_id"] in (C2, C3, C4))

    assert top_center_index(lifted) < top_center_index(base)         # centers move up
    assert _rank_ids(base) != _rank_ids(lifted)


# --------------------------------------------------------------------------- #
# 4. the explanation payload
# --------------------------------------------------------------------------- #

def test_recommendation_payload_is_self_consistent_and_marks_punts():
    cfg = _cfg([("PG", 1), ("SG", 1), ("SF", 1), ("C", 1), ("UTIL", 1), ("BENCH", 2)])
    team = _team(cfg, [C1, C2, C3])                      # heavy blk/reb, thin ast/fg3m
    inferred = set(team.detect_punts())
    assert SC.AST in inferred                            # this roster has conceded assists

    recs = recommend(team, POOL, [G1, G2, F1, C4], n=99)
    assert recs, "expected some legal recommendations"
    for r in recs:
        pc = r["marginal"]["per_category"]
        assert sum(pc.values()) == pytest.approx(r["marginal"]["delta_expected_wins"], abs=1e-9)
        assert r["legal"] is True
        assert r["slot_fit"]                             # non-empty
        assert set(r["punts"]) == inferred

    g1_row = next(r for r in recs if r["nba_id"] == G1)
    assert g1_row["punts"][SC.AST] == "pushes"           # a high-assist guard un-punts ast
    c4_row = next(r for r in recs if r["nba_id"] == C4)
    assert c4_row["punts"][SC.AST] == "respects"         # another center does not


# --------------------------------------------------------------------------- #
# 5. filtering
# --------------------------------------------------------------------------- #

def test_recommend_drops_roster_members_and_illegal_candidates():
    cfg = _cfg([("PG", 1), ("SG", 1), ("C", 1)])
    team = _team(cfg, [G1, G2])                          # guard slots full, C open
    recs = recommend(team, POOL, [G1, G3, C1], n=99)     # G1 rostered, G3 illegal, C1 legal
    ids = _rank_ids(recs)
    assert ids == [C1]

    with_illegal = recommend(team, POOL, [G1, G3, C1], n=99, include_illegal=True)
    ids2 = _rank_ids(with_illegal)
    assert G1 not in ids2                                # rostered players are never candidates
    assert set(ids2) == {G3, C1}
    assert ids2[-1] == G3                                # illegal sorts below every legal one
    assert next(r for r in with_illegal if r["nba_id"] == G3)["legal"] is False


def test_recommend_respects_n_and_empty_input():
    cfg = _cfg([("PG", 1), ("SG", 1), ("C", 1), ("UTIL", 1), ("BENCH", 2)])
    team = _team(cfg, [G1])
    assert recommend(team, POOL, [], n=5) == []
    assert recommend(team, POOL, [G2, G3, C1], n=0) == []
    assert len(recommend(team, POOL, [G2, G3, G4, C1, C2], n=2)) == 2


# --------------------------------------------------------------------------- #
# 6. layering
# --------------------------------------------------------------------------- #

def test_engine_draft_does_not_import_cli():
    src = (pathlib.Path(__file__).resolve().parents[1] / "engine" / "draft.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+cli(\.|\s|$)", src, re.M)
