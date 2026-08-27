"""
engine/team.py -- E1, the roster-state object (the roster-aware layer).

WHY THIS EXISTS. Every feature before this one ranks players against a static
pool: engine/value.py's board is the same for everyone in a league. But in a
category league a player's value depends on the roster he is joining. A third
elite shot-blocker is worth less to a team already winning blocks; a punt-FT%
team should happily trade away a 90% free-throw shooter. compute_values()
structurally cannot say that -- it has no notion of "my team so far". This
object does, and per SCOPE.md it is the genuinely differentiated part of the
project. The draft assistant and trade analyzer (next session) both consume it.

WHAT IT GIVES YOU.
  * category_totals()  -- this roster's per-category accumulation, with ratio
                          categories (FG%, FT%) aggregated BY VOLUME, not averaged.
  * standing()         -- each category total expressed as a z-score against the
                          mean and spread of what a typical team in THIS league
                          would accumulate (see "the league baseline" below).
  * expected_category_wins() -- the objective: sum over contested categories of
                          the probability this roster beats a typical team there.
  * marginal_value(cand)     -- how much adding one candidate moves that objective,
                          per category and in total. The primitive the draft
                          assistant calls. Exact recompute, not a linearization.
  * detect_punts()     -- categories the roster has effectively conceded, inferred
                          from the standings. REPORTED, never written back to the
                          config -- an inferred punt and a configured punt are
                          deliberately kept separate.
  * player_breakdown() -- per rostered player, the per-category z-contribution
                          from engine/diagnose.category_contributions (reused
                          verbatim -- this module does not re-derive the z math).

THE LEAGUE BASELINE ("relative to the league").
standing() needs "what a typical team in this league accumulates" in every
category. It is derived from the draftable pool, not hardcoded:
  1. Take the eligible players, apply the basis, and pick the top
     num_teams * roster_slots by the same category value engine/value.py uses
     (_draft_pool_ids) -- this is the draftable pool.
  2. Partition that pool into num_teams synthetic rosters:
       - baseline_method="snake" (default): deal the value-sorted pool in snake
         order, so every synthetic team gets a similar tier mix.
       - baseline_method="random": a fixed-seed deterministic shuffle then a
         round-robin deal.
  3. For each category, compute every synthetic team's total the SAME
     volume-correct way category_totals() does, then take the mean and standard
     deviation across the num_teams synthetic totals. That (mu_c, sigma_c) pair
     is the league baseline for category c.

BIAS NOTE. Snake-dealing a value-ordered pool makes every synthetic team a
near-identical tier mix, which understates sigma_c relative to a real league
containing punt builds and lopsided rosters. A smaller sigma_c inflates every
|z| this object reports (and therefore the magnitude of marginal values).
"random" spreads teams out more and is a looser lower bound on sigma_c; a real
league is looser still. _demo() prints sigma_c under both methods so the
sensitivity is visible. Treat standing z-scores as ordinally trustworthy and
their absolute magnitude as an overestimate.

MID-DRAFT PRORATION. A partial roster holds fewer players than a complete
synthetic team, so its counting-stat totals are not comparable to a full team's.
standing() scales the counting-cat baseline (mean and std) by
current_roster_size / total_roster_slots -- so it reads as "am I on pace for the
picks I've spent," and detect_punts() is meaningful before the draft is over.
Ratio cats are rates and are not scaled. marginal_value() pins both of its
endpoints to one proration size (the post-move roster size) so the delta is the
player, not a moving yardstick.

THE OBJECTIVE, and h2h vs roto.
For category c, model this roster's total and a typical opponent's total as two
independent draws from the synthetic-team distribution N(mu_c, sigma_c). Then
    P(win c) = Phi( z_c / sqrt(2) ),   z_c = (total_c - mu_c) / sigma_c
(sign-flipped for turnovers). Two reporting units, selected by
config.matchup_format unless overridden:
    units="category_wins" (h2h):  objective_c = P(win c)
    units="roto_points"   (roto):  objective_c = 1 + (num_teams - 1) * P(win c)
The roto form is an AFFINE transform of the h2h form with positive slope
(num_teams - 1) and a constant offset that cancels in any marginal difference,
so BOTH UNITS RANK ROSTERS IDENTICALLY under this model. That is a deliberate
consequence of the model being variance-free: every category shares the same
implicit matchup variance (the sqrt(2)). The two formats separate only once a
category-specific weekly variance tau_c is estimated -- in h2h a matchup is a
tiny sample, so high-variance categories (blk, stl, fg3m, thin-volume FT%)
compress toward a coin flip, P(win c) -> Phi(z_c / sqrt(2 + tau_c**2)); roto is
season-long and barely feels tau_c. tau_c is not estimable without game logs,
so it is not modeled here. See BUILD_PLAN.md -> "M5" for the game-log ingest.

RATIO CATEGORIES DO NOT SUM. A roster's FG% is total makes over total attempts,
never the mean of its players' FG%. _team_cat_total() aggregates ratio cats as
sum(makes) / sum(attempts) (falling back to fg_pct * fga for makes when a line
lacks fgm, e.g. a partial rookie line) -- the same volume weighting
engine/value._pool_ratio_pct does for the pool. The z-score trap (never z-score
raw player percentages) is resolved at the player -> team aggregation step:
once each synthetic team and this team each hold a single volume-correct
percentage, z-scoring those already-aggregated team rates against each other is
legitimate.

Run from the repo root:
    python engine/team.py            # a worked roster on the projected pool
    python engine/team.py --selftest # offline checks, no network
"""

from __future__ import annotations

import math
import os
import random
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engine.diagnose import category_contributions
from engine.league_config import CATEGORY_META, MatchupFormat, ScoringType
from engine.value import (
    AVAIL_ALPHA, Z_CAP, _apply_basis, _draft_pool_ids, _mean_std,
)

BASELINE_METHODS = ("snake", "random")
UNITS = ("category_wins", "roto_points")

# baseline_method="random" must be deterministic so tests and CI are stable.
_RANDOM_SEED = 20260826

# Inferred-punt threshold: a category whose standing z is at or below this is
# reported as effectively conceded. -1.5 is roughly "you lose this category most
# weeks"; the prompt's "three standard deviations" is available as a stricter
# setting via the threshold argument. Not fit to anything -- there is no
# in-season data to fit it on yet.
PUNT_Z = -1.5

# Makes stat backing each ratio category's attempts stat.
_MADE_FOR_VOLUME = {"fga": "fgm", "fta": "ftm"}


def _phi(x: float) -> float:
    """Standard-normal CDF via math.erf (stdlib -- no scipy dependency)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _win_prob(z: float) -> float:
    """P(this roster beats a typical league team in a category), both modeled as
    independent draws from the synthetic-team distribution: Phi(z / sqrt(2))."""
    return _phi(z / math.sqrt(2.0))


def _team_cat_total(lines: dict, ids, cat) -> float:
    """One roster's total in category `cat`. Counting cats sum; ratio cats are
    aggregated by volume -- sum(makes) / sum(attempts) -- never averaged."""
    meta = CATEGORY_META[cat]
    if not meta.is_ratio:
        return sum((lines[i].get(cat.value) or 0.0) for i in ids if i in lines)
    vol_key = meta.volume_stat
    made_key = _MADE_FOR_VOLUME[vol_key]
    attempts = sum((lines[i].get(vol_key) or 0.0) for i in ids if i in lines)
    if attempts == 0:
        return 0.0
    makes = 0.0
    for i in ids:
        p = lines.get(i)
        if p is None:
            continue
        m = p.get(made_key)
        if m is None:                       # partial line (e.g. a rookie): reconstruct
            m = (p.get(cat.value) or 0.0) * (p.get(vol_key) or 0.0)
        makes += m
    return makes / attempts


class Team:
    """A drafted roster (nba_ids) against a LeagueConfig, plus the per-category
    standing it holds relative to the league.

    Parameters
    ----------
    config : LeagueConfig
        Must be a category league. POINTS leagues have no per-category standing
        (there is no category z-scoring to stand in) -- constructing a Team from
        one raises ValueError; use engine/value.compute_values for points.
    players : dict[int, dict]
        The full player pool keyed by nba_id -- projected lines for a draft,
        or actual lines for an in-season check. Same shape the rest of the
        engine passes around (per-game pts/reb/.../fgm/fga/ftm/fta, plus gp).
    roster_ids : iterable[int]
        The nba_ids already drafted onto this team. May be empty (pre-draft).
    min_gp : int
        Eligibility floor for the league baseline pool (default 20, matching
        compute_values). Rostered / candidate players below it still contribute
        -- their availability-adjusted line is simply small.
    basis : str
        "availability_adjusted" (default, matching compute_values so a user
        reading board rank and marginal value side by side is on one basis) or
        "per_game".
    pool_size : int | None
        Size of the draftable pool the baseline is built from. Defaults to
        num_teams * (sum of roster slot counts), matching compute_values.
    z_cap : float
        Standings z-scores are clamped to +/- this (default Z_CAP = 3.0), as
        elsewhere in the engine -- keeps a degenerate sigma from producing an
        absurd win probability.
    baseline_method : str
        "snake" (default) or "random"; see the module docstring's BIAS NOTE.
    """

    def __init__(self, config, players: dict, roster_ids=(), *, min_gp: int = 20,
                 basis: str = "availability_adjusted", pool_size: int | None = None,
                 z_cap: float = Z_CAP, avail_alpha: float = AVAIL_ALPHA,
                 baseline_method: str = "snake"):
        if config.scoring_type is ScoringType.POINTS:
            raise ValueError(
                "Team models per-category standing; a POINTS league has no "
                "category standing to model. Use engine/value.compute_values."
            )
        if baseline_method not in BASELINE_METHODS:
            raise ValueError(
                f"baseline_method must be one of {BASELINE_METHODS}, got {baseline_method!r}"
            )
        self.config = config
        self.min_gp = min_gp
        self.basis = basis
        self.baseline_method = baseline_method
        self._z_cap = z_cap
        self._avail_alpha = avail_alpha

        self.roster_ids: set = set(roster_ids)

        # Rostered / candidate lookups read this (every player, basis-scaled).
        self._all_scaled = _apply_basis(players, basis, avail_alpha)
        # The league baseline is built from eligible players only.
        self._eligible = {i: p for i, p in players.items() if (p.get("gp") or 0) >= min_gp}
        self._scaled = _apply_basis(self._eligible, basis, avail_alpha)

        slots_per_team = sum(getattr(s, "count", 0) for s in config.roster) or 13
        self._slots_per_team = slots_per_team
        if pool_size is None:
            pool_size = config.num_teams * slots_per_team
        self.pool_size = max(1, min(pool_size, len(self._scaled)))
        self._pool_ids = _draft_pool_ids(self._scaled, config, self.pool_size, z_cap)

        # Lazy {cat: (mu, sigma)}; depends only on the pool, so adding/removing a
        # rostered player never invalidates it.
        self._baseline: dict | None = None

    # -- roster mutation ---------------------------------------------------- #

    def add(self, nba_id: int) -> "Team":
        self.roster_ids.add(nba_id)
        return self

    def remove(self, nba_id: int) -> "Team":
        self.roster_ids.discard(nba_id)
        return self

    # -- the league baseline --------------------------------------------------- #

    def _synthetic_teams(self) -> list[list]:
        """num_teams synthetic rosters partitioned from the top of the pool."""
        n = self.config.num_teams
        take = list(self._pool_ids[: n * self._slots_per_team])
        teams: list[list] = [[] for _ in range(n)]
        if self.baseline_method == "random":
            order = take[:]
            random.Random(_RANDOM_SEED).shuffle(order)
            for idx, pid in enumerate(order):
                teams[idx % n].append(pid)
        else:  # snake: deal the value-sorted pool back and forth
            for idx, pid in enumerate(take):
                rnd, slot = divmod(idx, n)
                teams[slot if rnd % 2 == 0 else n - 1 - slot].append(pid)
        return teams

    def league_baseline(self) -> dict:
        """{StatCategory: (mean, std)} across the num_teams synthetic rosters."""
        if self._baseline is None:
            teams = self._synthetic_teams()
            self._baseline = {}
            for cat in self.config.active_categories:
                totals = [_team_cat_total(self._scaled, t, cat) for t in teams]
                self._baseline[cat] = _mean_std(totals)
        return self._baseline

    # -- this roster's standing --------------------------------------------- #

    def _roster_ids_with(self, extra_ids, drop_ids) -> set:
        return (self.roster_ids | set(extra_ids)) - set(drop_ids)

    def category_totals(self, *, extra_ids=(), drop_ids=()) -> dict:
        """{StatCategory: float} for this roster (optionally +extra_ids/-drop_ids).
        Counting cats sum; ratio cats are volume-weighted makes/attempts."""
        ids = self._roster_ids_with(extra_ids, drop_ids)
        return {cat: _team_cat_total(self._all_scaled, ids, cat)
                for cat in self.config.active_categories}

    def standing(self, *, extra_ids=(), drop_ids=(), prorate_to=None) -> dict:
        """{StatCategory: {total, league_mean, league_std, z, win_prob}}.

        z is (total - mean) / std against the synthetic-team distribution,
        sign-flipped so + always means "winning the category", clamped to
        +/- z_cap. win_prob is Phi(z / sqrt(2)).

        PRORATION. A roster mid-draft holds fewer players than a complete
        synthetic team, so its counting-stat totals are not comparable to a full
        team's. The counting-cat baseline (mean and std) is therefore scaled by
        n_slots / total_roster_slots, where n_slots defaults to the current
        roster size (pass prorate_to to pin it -- marginal_value does, so its two
        endpoints share one yardstick). standing() then reads as "am I on pace,
        given how many picks I've made." Ratio cats (FG%, FT%) are rates and are
        NOT scaled. An empty roster (n_slots == 0) has no standing: every z is 0.

        (A sharper baseline would compare your first k picks to the synthetic
        teams' first k picks rather than to a linear k/N slice of a full team;
        linear proration is the v1 choice -- simple and adequate for the draft
        assistant. See BUILD_PLAN.md.)
        """
        ids = self._roster_ids_with(extra_ids, drop_ids)
        n_slots = len(ids) if prorate_to is None else prorate_to
        frac = n_slots / self._slots_per_team if self._slots_per_team else 0.0
        base = self.league_baseline()
        totals = self.category_totals(extra_ids=extra_ids, drop_ids=drop_ids)
        out = {}
        for cat in self.config.active_categories:
            mu, sigma = base[cat]
            if not CATEGORY_META[cat].is_ratio:      # counting cats scale with roster size
                mu, sigma = mu * frac, sigma * frac
            if n_slots <= 0:
                z = 0.0
            else:
                raw = (totals[cat] - mu) / sigma if sigma else 0.0
                if not CATEGORY_META[cat].higher_is_better:
                    raw = -raw
                z = max(-self._z_cap, min(self._z_cap, raw))
            out[cat] = {
                "total": totals[cat],
                "league_mean": mu,
                "league_std": sigma,
                "z": z,
                "win_prob": _win_prob(z),
            }
        return out

    # -- the objective ---------------------------------------------------------- #

    def _resolve_units(self, units) -> str:
        if units is None:
            units = ("roto_points"
                     if self.config.matchup_format is MatchupFormat.ROTO
                     else "category_wins")
        if units not in UNITS:
            raise ValueError(f"units must be one of {UNITS}, got {units!r}")
        return units

    def _objective_c(self, win_prob: float, units: str) -> float:
        if units == "category_wins":
            return win_prob
        return 1.0 + (self.config.num_teams - 1) * win_prob    # roto_points

    def expected_category_wins(self, *, extra_ids=(), drop_ids=(),
                               ignore_categories=(), units=None,
                               prorate_to=None) -> float:
        """Objective value for this roster: sum over contested categories of the
        per-category objective.

        units : None (default -> from config.matchup_format), "category_wins",
            or "roto_points". The two units are an affine transform of each other
            and rank rosters identically -- see the module docstring.
        ignore_categories : categories to drop from the sum (e.g. inferred punts
            the caller has decided to concede). active_categories already
            excludes configured punts.
        prorate_to : baseline-size override, forwarded to standing().
        """
        units = self._resolve_units(units)
        ignore = set(ignore_categories)
        st = self.standing(extra_ids=extra_ids, drop_ids=drop_ids, prorate_to=prorate_to)
        return sum(self._objective_c(row["win_prob"], units)
                   for cat, row in st.items() if cat not in ignore)

    def marginal_value(self, candidate_id: int, *, drop_id=None, units=None,
                       ignore_categories=()) -> dict:
        """How much adding `candidate_id` (optionally in place of `drop_id`) moves
        the objective. The primitive the draft assistant and trade analyzer call.

        Exact recompute -- the candidate is added to a copy of the roster and the
        standing is re-derived against the same (unchanged) league baseline, so
        the returned deltas capture diminishing returns exactly: the same player
        is worth less in a category this roster already dominates (the sigmoid
        is flat out there) than in a contested one (where it is steep).

        Both endpoints share one proration yardstick -- the roster's size AFTER
        the move (floored at 1) -- so the delta reflects the player, not a
        shift in the baseline between the two evaluations.

        Returns
        -------
        {nba_id, drop_id, units,
         before, after,                 -- objective value without / with the move
         delta_expected_wins,           -- after - before (name kept stable across
                                           units; it is roto points when
                                           units="roto_points")
         per_category: {StatCategory: float}}   -- sums to delta_expected_wins
        """
        units = self._resolve_units(units)
        ignore = set(ignore_categories)
        drop = () if drop_id is None else (drop_id,)
        n_ref = max(1, len(self._roster_ids_with((candidate_id,), drop)))
        before = self.standing(drop_ids=drop, prorate_to=n_ref)
        after = self.standing(extra_ids=(candidate_id,), drop_ids=drop, prorate_to=n_ref)

        per_cat, tot_before, tot_after = {}, 0.0, 0.0
        for cat in self.config.active_categories:
            if cat in ignore:
                continue
            b = self._objective_c(before[cat]["win_prob"], units)
            a = self._objective_c(after[cat]["win_prob"], units)
            per_cat[cat] = a - b
            tot_before += b
            tot_after += a
        return {
            "nba_id": candidate_id,
            "drop_id": drop_id,
            "units": units,
            "before": tot_before,
            "after": tot_after,
            "delta_expected_wins": tot_after - tot_before,
            "per_category": per_cat,
        }

    # -- punt detection --------------------------------------------------------- #

    def detect_punts(self, *, threshold: float = PUNT_Z, extra_ids=(), drop_ids=(),
                     prorate_to=None) -> dict:
        """{StatCategory: z} for every contested category whose standing z is at
        or below `threshold` -- categories this roster has effectively conceded.

        Uses the size-prorated standing (see standing()), so this is meaningful
        mid-draft, not only for a full roster.

        This is REPORTED, not applied: the config is never modified, and an
        inferred punt is kept separate from config.punted_categories (a
        deliberate build decision). A caller that wants to act on it passes the
        keys back as expected_category_wins(ignore_categories=...).
        """
        return {cat: row["z"]
                for cat, row in self.standing(extra_ids=extra_ids, drop_ids=drop_ids,
                                              prorate_to=prorate_to).items()
                if row["z"] <= threshold}

    # -- per-player attribution (reused z math) ------------------------------- #

    def player_breakdown(self) -> dict:
        """{nba_id: category_contributions(...) or None} for each rostered player.

        The per-category z-contribution of each player against THIS team's draft
        pool, straight from engine/diagnose.category_contributions -- so the
        trade analyzer can see which players carry which categories without
        re-deriving the z math. None for a rostered player below min_gp (that
        function's own eligibility rule).
        """
        return {
            pid: category_contributions(
                self._eligible, self.config, pid, min_gp=self.min_gp,
                basis=self.basis, z_cap=self._z_cap, avail_alpha=self._avail_alpha,
                pool_ids=self._pool_ids,
            )
            for pid in self.roster_ids
        }


# --------------------------------------------------------------------------- #
# Demo: a worked roster on the real projected pool.
# --------------------------------------------------------------------------- #

def _demo() -> None:
    from ingest.nba_boxscores import get_season_boxscores, recent_completed_seasons
    from ingest.nba_rosters import attach_positions, load_rosters_or_warn
    from engine.projection import load_role_overrides, load_rookies, project_players
    from engine.league_config import standard_9cat
    from engine.value import compute_values
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

    # A deliberately blocks-heavy, FT%-shaky build (the SCOPE.md motivating case).
    wanted = ["Victor Wembanyama", "Anthony Davis", "Rudy Gobert",
              "Giannis Antetokounmpo", "Jarrett Allen"]
    roster = [by_name[n] for n in wanted if n in by_name]
    team = Team(cfg, projected, roster)

    slots = sum(s.count for s in cfg.roster)
    print(f"\nRoster ({len(roster)} of {slots} slots): " + ", ".join(
        projected[i].get("name") for i in roster))

    print(f"\nStanding vs a typical {cfg.num_teams}-team roster's first {len(roster)} "
          f"picks (snake baseline, counting cats prorated {len(roster)}/{slots}):")
    print(f"  {'cat':<8} {'team':>10} {'lg mean':>10} {'lg std':>10} {'z':>7} {'P(win)':>8}")
    for cat, row in team.standing().items():
        print(f"  {cat.value:<8} {row['total']:>10.2f} {row['league_mean']:>10.2f} "
              f"{row['league_std']:>10.2f} {row['z']:>7.2f} {row['win_prob']:>8.2f}")

    print("\nsigma_c sensitivity -- snake understates spread, inflating |z|:")
    snake_b = Team(cfg, projected, roster, baseline_method="snake").league_baseline()
    rand_b = Team(cfg, projected, roster, baseline_method="random").league_baseline()
    print(f"  {'cat':<8} {'snake':>10} {'random':>10} {'ratio':>8}")
    for cat in cfg.active_categories:
        s, r = snake_b[cat][1], rand_b[cat][1]
        print(f"  {cat.value:<8} {s:>10.3f} {r:>10.3f} {(r / s if s else 0):>8.2f}")

    h2h = team.expected_category_wins(units="category_wins")
    roto = team.expected_category_wins(units="roto_points")
    n_active = len(cfg.active_categories)
    print(f"\nobjective: {h2h:.2f} / {n_active} expected category wins (h2h)")
    print(f"           {roto:.2f} expected roto points "
          f"(== {n_active} + {cfg.num_teams - 1} * h2h = "
          f"{n_active + (cfg.num_teams - 1) * h2h:.2f}; affine, same ranking)")

    punts = team.detect_punts()
    print("\ninferred punts (z <= {:.1f}): {}".format(
        PUNT_Z, ", ".join(f"{c.value} ({z:+.2f})" for c, z in punts.items()) or "none"))

    remaining = [r["nba_id"] for r in board
                 if r["nba_id"] not in team.roster_ids][:120]
    ranked = sorted((team.marginal_value(pid) for pid in remaining),
                    key=lambda m: m["delta_expected_wins"], reverse=True)
    print("\ntop 8 additions by marginal expected category wins:")
    for m in ranked[:8]:
        drivers = sorted(m["per_category"].items(), key=lambda kv: kv[1], reverse=True)[:3]
        why = ", ".join(f"{c.value} {v:+.3f}" for c, v in drivers)
        print(f"  {(projected[m['nba_id']].get('name') or '?'):<24} "
              f"{m['delta_expected_wins']:+.3f}   ({why})")


def _selftest() -> None:
    """Thin wrapper: runs tests/test_team.py under pytest."""
    import pytest

    rc = pytest.main(["-q", os.path.join(_ROOT, "tests", "test_team.py")])
    if rc != 0:
        raise SystemExit(rc)
    print("team selftest ok: see tests/test_team.py")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        _demo()
