"""
engine/draft.py -- E2, the draft assistant (engine half; surface-agnostic).

WHAT THIS IS. Given a Team (my roster + the league config + the player pool) and
the set of still-available players, rank the candidates I should draft next. The
ranking is roster-aware: it is driven by engine/team.marginal_value (how much a
player moves my expected category wins) rather than by the static VOR board every
fantasy site already ships. cli/draft.py (next session) is the only front door;
this module does no I/O and imports nothing from cli/.

THREE THINGS THE RAW marginal_value RANKING DOES NOT HANDLE, added here:

1. ROSTER-SLOT LEGALITY.  A candidate is only recommendable if some open roster
   slot admits his position, respecting multi-position eligibility. legal_additions()
   solves the full assignment (bipartite matching of my whole roster + the
   candidate into distinct slots), not a greedy "is any single slot free" check --
   a guard can be blocked even with an empty bench if every slot he is eligible
   for is already spoken for by another eligible player.

   GRANULARITY LIMIT. Positions come from ingest/nba_rosters.py, which only
   distinguishes G / F / C (CommonTeamRoster has no PG/SG or SF/PF split -- see
   that module). So every guard is eligible for BOTH a PG and an SG slot, and the
   gate genuinely constrains only across the G/F/C boundary (and against a full
   bench). slot_fit() reports "PG/SG/G/UTIL" for a guard, not "SG" -- the finer
   label would imply precision the data does not have. A player with no position
   on file (retired / overseas / unsigned) is treated as UTIL/BENCH-only, not as
   a wildcard -- a recommendation you cannot legally make is a bug. If NO player
   in the roster or candidate list has a position, the gate switches off entirely
   (same degradation as engine/value.py's positional replacement).

2. EARLY-DRAFT BLENDING.  At pick 1 the roster carries almost no signal:
   marginal_value's "before" standing is a near-empty prior, every category near a
   coin flip, so the delta collapses toward a compressed monotone-in-raw-z number
   dominated by one or two extreme categories -- and it matches how good drafters
   actually pick early ("best available"). At pick S (starters full) the roster
   shape is real, your punts and strengths are locked, and a static VOR now
   actively misleads (it cannot see that you already stacked blocks). So blend:

       w_mv(k) = clamp(k / S, 0, 1)          k = current roster size
                                             S = starter slots (non-BENCH)
       score(c) = w_mv * z(marginal_delta) + (1 - w_mv) * z(vor)
                  + scarcity_lambda * scarcity(c)

   z(.) is a z-score across the legal candidate set, so the two signals share one
   scale. Linear in k is the minimal assumption -- each pick adds roughly one
   observation of your category shape, and after the Part-1 proration fix there is
   no evidence for a specific curve shape. k / S (not (k-1)/(S-1)): one real pick
   already carries signal and should get nonzero weight. blend= overrides: "vor"
   (0.0), "marginal" (1.0), or a fixed float.

   The VOR term uses the CONFIG-DEFAULT replacement (flat), computed over the
   REMAINING pool. Positional (strict/hybrid) replacement was rejected empirically
   for this project (BUILD_PLAN.md Decision c: hybrid +0.000 Spearman, strict
   penalizes centers via the eligible-pool asymmetry); putting it here would
   relitigate that and double-count scarcity against term 3.

3. POSITIONAL SCARCITY over the REMAINING pool.  positional_pressure() reports
   demand / supply per G/F/C: demand is the league-wide count of dedicated slots
   for that group (static -- other teams' rosters are not modeled), supply is the
   count of still-available players eligible for it within a relevance window
   (top num_teams * S of the remaining board). A ratio > 1 means the position is
   drying up. This is ALWAYS printed as context. It only moves the ranking if the
   caller sets scarcity_lambda > 0: the default is 0.0. lambda, the relevance
   window, and the blend curve are all UNVALIDATED constants -- there is no draft
   simulation behind them yet. If scarcity proves to matter it earns its own
   session with a sweep. Until then: shown, not applied.

OUTPUT.  recommend() returns, per candidate, the full marginal_value dict (so a
caller can show which categories he moves and by how much), which of the team's
INFERRED punts (Team.detect_punts) he respects vs pushes, the slot(s) he fills,
and the blend weight in effect. cli/draft.py renders this --explain-style.

Note on the punt map: early in a draft a lopsided-incomplete roster infers punts
it has not really chosen (four bigs read as "punting assists" simply because no
guard is on the roster yet), so mid-draft "pushes" reads as "addresses a current
weakness" more than "breaks a strategic punt." It sharpens as the roster fills.

Run from the repo root:
    python engine/draft.py            # a worked mid-draft board on the projected pool
    python engine/draft.py --selftest # offline checks, no network
"""

from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engine.value import SLOT_GROUP, _mean_std, compute_values

BENCH_SLOT = "BENCH"
DEFAULT_RECS = 10

# scarcity_lambda default: OFF. positional_pressure() is reported for context but
# does not move the ranking unless a caller opts in. See the module docstring --
# this, the relevance window, and the blend curve are unvalidated pending a
# draft simulation.
DEFAULT_SCARCITY_LAMBDA = 0.0

# "pushes an inferred punt" threshold: a per-category marginal delta above this
# (in category-win units; scaled by num_teams-1 for roto_points) means the
# candidate meaningfully contests a category the roster has conceded.
_PUNT_PUSH_EPS = 0.01


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _starter_slot_count(config) -> int:
    """Total non-BENCH roster slots (UTIL counts as a starter)."""
    return sum(s.count for s in config.roster if s.position != BENCH_SLOT)


def player_groups(players: dict, pid: int) -> tuple:
    """The candidate's eligibility groups, e.g. ('G',) or ('F', 'G'); () if the
    player has no position on file."""
    return tuple((players.get(pid) or {}).get("position") or ())


def _slot_group(slot_position: str) -> str | None:
    """G/F/C for a dedicated slot, or None for a position-blind slot (UTIL,
    BENCH, or any slot name engine/value.SLOT_GROUP does not recognize)."""
    return SLOT_GROUP.get(slot_position, None)


def _slot_positions(config) -> list[str]:
    """The roster expanded to one entry per slot, e.g.
    ['PG', 'SG', 'SF', 'PF', 'C', 'G', 'F', 'UTIL', 'UTIL', 'BENCH', ...]."""
    return [s.position for s in config.roster for _ in range(s.count)]


def _zscore(d: dict) -> dict:
    """{key: (v - mean) / std} across d's values; all-zero if std is 0."""
    mu, sd = _mean_std(list(d.values()))
    if sd == 0:
        return {k: 0.0 for k in d}
    return {k: (v - mu) / sd for k, v in d.items()}


def blend_weight(team, blend="auto") -> float:
    """The weight on the marginal-value signal (vs raw VOR) for this roster.

    "auto" -> clamp(len(roster) / starter_slots, 0, 1). "vor" -> 0.0,
    "marginal" -> 1.0, or pass a number in [0, 1] to pin it.
    """
    if blend == "vor":
        return 0.0
    if blend == "marginal":
        return 1.0
    if isinstance(blend, (int, float)) and not isinstance(blend, bool):
        return max(0.0, min(1.0, float(blend)))
    if blend != "auto":
        raise ValueError(
            f"blend must be 'auto', 'vor', 'marginal', or a number in [0, 1], got {blend!r}"
        )
    starters = _starter_slot_count(team.config)
    if starters <= 0:
        return 1.0
    return max(0.0, min(1.0, len(team.roster_ids) / starters))


# --------------------------------------------------------------------------- #
# 1. roster-slot legality
# --------------------------------------------------------------------------- #

def _augment(pid, groups_of, slot_positions, seen, slot_of) -> bool:
    """One Kuhn's augmenting-path step: try to seat player `pid` in some slot,
    bumping whoever is there to another slot if needed. Mutates slot_of."""
    for s, pos in enumerate(slot_positions):
        if s in seen:
            continue
        g = _slot_group(pos)
        if not (g is None or g in groups_of[pid]):
            continue
        seen.add(s)
        if slot_of[s] is None or _augment(slot_of[s], groups_of, slot_positions, seen, slot_of):
            slot_of[s] = pid
            return True
    return False


def legal_additions(team, players: dict, candidate_ids) -> set:
    """The subset of candidate_ids that could legally join this roster.

    A candidate is legal iff the whole roster PLUS that candidate can be
    simultaneously assigned to distinct roster slots (respecting G/F/C
    eligibility; UTIL/BENCH admit anyone). Candidates already on the roster, or
    absent from `players`, are never legal.

    If no player anywhere (roster or candidates) has a position on file, the
    gate cannot say anything useful and every candidate is returned as legal --
    matching engine/value.py's fall back to the flat pool when position data is
    missing.
    """
    roster = [pid for pid in team.roster_ids if pid in players]
    cands = [c for c in dict.fromkeys(candidate_ids)
             if c in players and c not in team.roster_ids]
    if not cands:
        return set()

    slot_positions = _slot_positions(team.config)
    groups_of = {pid: player_groups(players, pid) for pid in roster + cands}

    if not any(groups_of[pid] for pid in roster + cands):
        return set(cands)                       # no position data -> gate off

    # Seat the fixed roster once; then each candidate needs just one more
    # augmenting path from the resulting matching.
    slot_of: list = [None] * len(slot_positions)
    for pid in roster:
        if not _augment(pid, groups_of, slot_positions, set(), slot_of):
            # The roster itself does not fit the slot template (over-full, or a
            # config mismatch). Nothing sensible to gate on -> everyone legal.
            return set(cands)

    legal = set()
    for c in cands:
        trial = slot_of[:]
        if _augment(c, groups_of, slot_positions, set(), trial):
            legal.add(c)
    return legal


def slot_fit(team, players: dict, candidate_id: int) -> tuple:
    """The roster-slot names this candidate is position-eligible for (ignoring
    who currently occupies them), in config order, e.g. ('PG', 'SG', 'G',
    'UTIL', 'BENCH'). Only meaningful when position data exists; a player with
    no position returns just the position-blind slots.
    """
    groups = player_groups(players, candidate_id)
    fits = []
    for s in team.config.roster:
        g = _slot_group(s.position)
        if g is None or g in groups:
            fits.append(s.position)
    return tuple(dict.fromkeys(fits))


# --------------------------------------------------------------------------- #
# 3. positional scarcity over the remaining pool
# --------------------------------------------------------------------------- #

def positional_pressure(team, players: dict, available_ids, *,
                        relevance_window: int | None = None) -> dict:
    """{'G'/'F'/'C': demand / supply} over the REMAINING pool.

    demand = league-wide dedicated slots for the group (num_teams * per-team
    count). supply = still-available players eligible for the group, counted
    within the top `relevance_window` of the remaining VOR board (default
    num_teams * starter_slots -- roughly "still startable"). > 1 means the
    position is drying up. Static demand: other teams' rosters are not modeled,
    so the dynamic part is supply shrinking as players come off the board.
    """
    cfg = team.config
    starters = _starter_slot_count(cfg)
    window = relevance_window if relevance_window is not None else cfg.num_teams * max(starters, 1)

    avail = [c for c in dict.fromkeys(available_ids)
             if c in players and c not in team.roster_ids]
    sub = {i: players[i] for i in avail}
    board = compute_values(sub, cfg, min_gp=team.min_gp, basis=team.basis,
                           avail_alpha=team.avail_alpha)
    top = [r["nba_id"] for r in board[:window]]

    out = {}
    for g in ("G", "F", "C"):
        demand = cfg.num_teams * sum(s.count for s in cfg.roster if _slot_group(s.position) == g)
        supply = sum(1 for pid in top if g in player_groups(players, pid))
        out[g] = demand / max(supply, 1)
    return out


# --------------------------------------------------------------------------- #
# the recommendation
# --------------------------------------------------------------------------- #

def recommend(team, players: dict, available_ids, *, n: int = DEFAULT_RECS,
              blend="auto", units=None, scarcity_lambda: float = DEFAULT_SCARCITY_LAMBDA,
              relevance_window: int | None = None, include_illegal: bool = False) -> list[dict]:
    """Rank the candidates this roster should draft next.

    Parameters
    ----------
    team : engine.team.Team           -- my roster + config + player pool
    players : dict[int, dict]         -- the full pool (same lines the Team holds)
    available_ids : iterable[int]     -- still-undrafted candidates to rank
    n : int                          -- how many recommendations to return
    blend : "auto" | "vor" | "marginal" | float in [0, 1]
        Weight schedule for marginal-value vs raw VOR; see blend_weight().
    units : None | "category_wins" | "roto_points"
        Reporting units for the marginal deltas (None -> config.matchup_format).
        The two are affine and rank identically.
    scarcity_lambda : float
        Weight on the positional-scarcity term. Default 0.0 (reported, not
        applied) -- see the module docstring.
    relevance_window : int | None    -- forwarded to positional_pressure()
    include_illegal : bool
        If True, roster-illegal candidates are still returned (flagged
        legal=False and sorted below every legal one) instead of dropped.

    Returns
    -------
    list[dict], highest score first, length <= n. Each row:
        nba_id, name, legal, score, w_mv, mv_z, vor_z, scarcity,
        slot_fit  -- tuple of eligible slot names,
        marginal  -- the full Team.marginal_value(nba_id) dict,
        punts     -- {inferred-punt category: "respects" | "pushes"},
        pressure  -- positional_pressure() for the whole board (same for all rows).
    """
    units = team.resolve_units(units)
    seen_ids = [c for c in dict.fromkeys(available_ids)
                if c in players and c not in team.roster_ids]
    if not seen_ids or n <= 0:
        return []

    legal = legal_additions(team, players, seen_ids)
    ranked_ids = seen_ids if include_illegal else [c for c in seen_ids if c in legal]
    if not ranked_ids:
        return []

    w = blend_weight(team, blend)

    # -- signal 1: marginal expected category wins -----------------------------
    mv = {c: team.marginal_value(c, units=units) for c in ranked_ids}
    mv_z = _zscore({c: mv[c]["delta_expected_wins"] for c in ranked_ids})

    # -- signal 2: raw VOR over the remaining pool (flat replacement) ---------
    sub = {i: players[i] for i in ranked_ids}
    vboard = compute_values(sub, team.config, min_gp=team.min_gp, basis=team.basis,
                            avail_alpha=team.avail_alpha)
    vor_raw = {r["nba_id"]: r["value"] for r in vboard}
    if vor_raw:
        floor = min(vor_raw.values())
        for c in ranked_ids:                    # sub-min_gp candidates: rank last
            vor_raw.setdefault(c, floor)
    else:
        vor_raw = {c: 0.0 for c in ranked_ids}
    vor_z = _zscore(vor_raw)

    # -- signal 3: positional scarcity (reported; applied only if lambda > 0) --
    pressure = positional_pressure(team, players, seen_ids, relevance_window=relevance_window)
    raw_scar = {c: max((pressure[g] for g in player_groups(players, c) if g in pressure),
                       default=0.0)
                for c in ranked_ids}
    scar_z = _zscore(raw_scar)
    scarcity = {c: max(0.0, scar_z[c]) for c in ranked_ids}     # only ever a bonus

    inferred = team.detect_punts()
    push_eps = _PUNT_PUSH_EPS * ((team.config.num_teams - 1) if units == "roto_points" else 1)

    rows = []
    for c in ranked_ids:
        per_cat = mv[c]["per_category"]
        rows.append({
            "nba_id": c,
            "name": (players[c] or {}).get("name"),
            "legal": c in legal,
            "score": w * mv_z[c] + (1.0 - w) * vor_z[c] + scarcity_lambda * scarcity[c],
            "w_mv": w,
            "mv_z": mv_z[c],
            "vor_z": vor_z[c],
            "scarcity": scarcity[c],
            "slot_fit": slot_fit(team, players, c),
            "marginal": mv[c],
            "punts": {cat: ("pushes" if per_cat.get(cat, 0.0) > push_eps else "respects")
                      for cat in inferred},
            "pressure": pressure,
        })

    rows.sort(key=lambda r: (r["legal"], r["score"]), reverse=True)
    return rows[:n]


# --------------------------------------------------------------------------- #
# Demo: a worked mid-draft board on the real projected pool.
# --------------------------------------------------------------------------- #

def _demo() -> None:
    from ingest.nba_boxscores import get_season_boxscores, recent_completed_seasons
    from ingest.nba_rosters import attach_positions, load_rosters_or_warn
    from engine.projection import load_role_overrides, load_rookies, project_players
    from engine.league_config import standard_9cat
    from engine.value import compute_values
    from engine.team import Team
    from util.console import configure_stdout_utf8

    configure_stdout_utf8()
    seasons = recent_completed_seasons(3)
    rosters = load_rosters_or_warn()
    lines = [get_season_boxscores(s)[0] for s in seasons]
    lines[0] = attach_positions(lines[0], rosters=rosters)
    projected, _ = project_players(lines, rookies=load_rookies(),
                                   role_overrides=load_role_overrides())

    cfg = standard_9cat()
    board = compute_values(projected, cfg)
    by_name = {r["name"]: r["nba_id"] for r in board}

    # A blocks-heavy, FT%-shaky start (the SCOPE.md motivating case), mid-draft.
    mine = ["Victor Wembanyama", "Rudy Gobert", "Jarrett Allen", "Anthony Davis"]
    roster = [by_name[nm] for nm in mine if nm in by_name]
    team = Team(cfg, projected, roster)
    available = [r["nba_id"] for r in board if r["nba_id"] not in team.roster_ids]

    starters = _starter_slot_count(cfg)
    w = blend_weight(team, "auto")
    print(f"\nMy roster ({len(roster)}/{starters} starters): "
          f"{', '.join(projected[i].get('name') for i in roster)}")
    punts = team.detect_punts()
    print("inferred punts: " + (", ".join(f"{c.value} ({z:+.2f})" for c, z in punts.items()) or "none"))
    print(f"blend weight on marginal value: w_mv = {w:.2f}  (= {len(roster)}/{starters})")

    pres = positional_pressure(team, projected, available)
    print("positional pressure (demand/supply, remaining pool): "
          + ", ".join(f"{g} {v:.2f}" for g, v in pres.items()))

    recs = recommend(team, projected, available, n=8)
    print("\ntop 8 picks (blended marginal value + VOR; scarcity reported, not applied):")
    for r in recs:
        drivers = sorted(r["marginal"]["per_category"].items(),
                         key=lambda kv: kv[1], reverse=True)[:3]
        why = ", ".join(f"{c.value} {v:+.3f}" for c, v in drivers)
        pushes = [c.value for c, verdict in r["punts"].items() if verdict == "pushes"]
        tag = f"  pushes punt: {', '.join(pushes)}" if pushes else ""
        print(f"  {(r['name'] or '?'):<24} score {r['score']:+.2f}  "
              f"[{'/'.join(r['slot_fit'][:3])}]  ({why}){tag}")


def _selftest() -> None:
    """Thin wrapper: runs tests/test_draft.py under pytest."""
    import pytest

    rc = pytest.main(["-q", os.path.join(_ROOT, "tests", "test_draft.py")])
    if rc != 0:
        raise SystemExit(rc)
    print("draft selftest ok: see tests/test_draft.py")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        _demo()
