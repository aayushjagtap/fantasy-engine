"""tests/fixtures.py -- shared synthetic fixtures for the test suite.

SIX_PLAYER_FIXTURE was duplicated verbatim between engine/value.py's and
engine/diagnose.py's --selftest blocks; it lives here once now.
"""
from __future__ import annotations

SIX_PLAYER_FIXTURE: dict[int, dict] = {
    1: dict(name="giannis_like", gp=51, pts=28, reb=11, ast=6, stl=0.9, blk=0.9,
            tov=3.2, fg3m=0.3, fg_pct=0.61, fga=18, ft_pct=0.60, fta=10),
    2: dict(name="sharp", gp=78, pts=22, reb=4, ast=6, stl=1.4, blk=0.3,
            tov=2.0, fg3m=3.6, fg_pct=0.47, fga=16, ft_pct=0.90, fta=5),
    3: dict(name="allround", gp=76, pts=20, reb=7, ast=7, stl=1.2, blk=0.6,
            tov=2.5, fg3m=2.0, fg_pct=0.50, fga=15, ft_pct=0.82, fta=6),
    4: dict(name="wing", gp=70, pts=14, reb=5, ast=2, stl=1.0, blk=0.5,
            tov=1.2, fg3m=1.8, fg_pct=0.46, fga=10, ft_pct=0.80, fta=3),
    5: dict(name="fillA", gp=72, pts=10, reb=4, ast=3, stl=0.7, blk=0.3,
            tov=1.5, fg3m=1.0, fg_pct=0.45, fga=9, ft_pct=0.78, fta=2),
    6: dict(name="fillB", gp=66, pts=9, reb=7, ast=1, stl=0.5, blk=1.2,
            tov=1.0, fg3m=0.2, fg_pct=0.56, fga=7, ft_pct=0.62, fta=4),
}

# Two otherwise-identical players differing only in games played -- isolates
# the availability-adjustment effect (SCALE_FIELDS) from everything else.
# compute_values()/_apply_basis() never mutate their input, so this dict is
# safe to share across multiple tests.
IRONMAN_FRAGILE_FIXTURE: dict[int, dict] = {
    10: dict(name="ironman", gp=80, pts=18, reb=6, ast=4, stl=1, blk=0.5, tov=2,
             fg3m=2, fg_pct=0.48, fga=13, ft_pct=0.8, fta=4),
    11: dict(name="fragile", gp=40, pts=18, reb=6, ast=4, stl=1, blk=0.5, tov=2,
             fg3m=2, fg_pct=0.48, fga=13, ft_pct=0.8, fta=4),
}


def rank_of(board: list[dict], name: str) -> int:
    """Helper matching the `rank = lambda b, nm: ...` pattern used in the old selftests."""
    return next(r["rank"] for r in board if r["name"] == name)
