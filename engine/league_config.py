"""
league_config.py -- the configuration contract for the fantasy basketball value engine.

This is the spine of the project. The value engine reads league settings from a
LeagueConfig object and NEVER hard-codes scoring assumptions. Swap the config and
the entire ranking board re-sorts. Supporting a new league means constructing a
new config, not editing engine code -- that is what "adapts to any league" means.

v1 target: express 9-category, points, and punt-build leagues.

Requires pydantic v2:  pip install "pydantic>=2"

A future TypeScript / extension consumer can get a language-agnostic contract via:
    LeagueConfig.model_json_schema()
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class ScoringType(str, Enum):
    CATEGORY = "category"   # head-to-head categories or rotisserie
    POINTS = "points"       # a single fantasy-point total


class MatchupFormat(str, Enum):
    """How category standings turn into an objective (category leagues only).

    Only the roster-aware layer (engine/team.py) reads this; the static value
    engine does not. Under engine/team.py's variance-free model the two formats
    produce *identical* roster rankings -- expected roto points in a category is
    an affine transform of the H2H category-win probability -- so this field
    selects reporting units, not a different optimum. They diverge only once a
    per-category weekly variance is modeled (needs game logs).
    """

    H2H = "h2h"      # maximize expected category wins per matchup
    ROTO = "roto"    # maximize season-long category standings points


class StatCategory(str, Enum):
    # Counting categories: raw totals, higher is better unless noted in CATEGORY_META.
    PTS = "pts"
    REB = "reb"
    AST = "ast"
    STL = "stl"
    BLK = "blk"
    FG3M = "fg3m"          # made three-pointers
    TOV = "tov"            # turnovers -- LOWER is better
    # Ratio categories: percentages that MUST be volume-weighted (see CATEGORY_META).
    FG_PCT = "fg_pct"
    FT_PCT = "ft_pct"


class CategoryMeta(BaseModel):
    """Engine-level facts about a category. Not user-configurable."""

    higher_is_better: bool = True
    is_ratio: bool = False              # ratio cats need volume weighting
    volume_stat: str | None = None      # the attempts stat backing a ratio cat


# The engine consults this registry to score each category correctly.
#
# Ratio categories (FG%, FT%) are the classic trap: you do NOT z-score the raw
# percentage, because that ignores volume -- a 90% FT shooter on 1 attempt/game
# is not more valuable than an 85% shooter on 9 attempts/game. The engine instead
# computes  impact = attempts * (player_pct - league_avg_pct)  and z-scores THAT.
# This registry tells it which cats are ratios and which attempts stat backs them.
CATEGORY_META: dict[StatCategory, CategoryMeta] = {
    StatCategory.PTS:    CategoryMeta(),
    StatCategory.REB:    CategoryMeta(),
    StatCategory.AST:    CategoryMeta(),
    StatCategory.STL:    CategoryMeta(),
    StatCategory.BLK:    CategoryMeta(),
    StatCategory.FG3M:   CategoryMeta(),
    StatCategory.TOV:    CategoryMeta(higher_is_better=False),
    StatCategory.FG_PCT: CategoryMeta(is_ratio=True, volume_stat="fga"),
    StatCategory.FT_PCT: CategoryMeta(is_ratio=True, volume_stat="fta"),
}


class CategorySetting(BaseModel):
    """Per-category league setting (category leagues only)."""

    enabled: bool = True
    weight: float = 1.0        # relative importance; usually 1.0
    is_punt: bool = False      # deliberately don't contest this category

    @model_validator(mode="after")
    def _punt_disables(self) -> "CategorySetting":
        # A punted category is one you never contest, so the engine excludes it
        # from value entirely. Punting is the single most important lever in
        # category leagues -- it re-sorts the whole board.
        if self.is_punt:
            self.enabled = False
        return self


class RosterSlot(BaseModel):
    position: str              # "PG", "SG", "G", "F", "C", "UTIL", "BENCH", ...
    count: int = Field(ge=0)


def _require_yaml():
    """YAML support is optional -- .json configs need no extra dependency."""
    try:
        import yaml
    except ImportError as e:
        raise ImportError(
            "Reading/writing a .yaml or .yml league config requires PyYAML "
            "(pip install pyyaml). YAML support is optional -- .json league "
            "configs work with no extra dependency."
        ) from e
    return yaml


class LeagueConfig(BaseModel):
    """The full league contract. Everything downstream reads from this."""

    name: str = "Untitled League"
    scoring_type: ScoringType
    num_teams: int = Field(default=12, ge=2, le=30)

    # --- CATEGORY mode ---
    categories: dict[StatCategory, CategorySetting] = Field(default_factory=dict)

    # --- POINTS mode ---
    # Raw box-score stat key -> points awarded per unit. Keys are stats such as
    # pts, reb, ast, stl, blk, tov, fg3m, fgm, fga, ftm, fta.
    point_values: dict[str, float] = Field(default_factory=dict)

    # Roster construction drives replacement-level baselines in the value engine.
    roster: list[RosterSlot] = Field(default_factory=list)

    games_per_week: int | None = None   # reserved for in-season features (v2)

    # Category leagues only; read by engine/team.py, ignored by the static value
    # engine. Defaults to h2h (the common case). See MatchupFormat.
    matchup_format: MatchupFormat = MatchupFormat.H2H

    @model_validator(mode="after")
    def _check_mode_fields(self) -> "LeagueConfig":
        if self.scoring_type is ScoringType.CATEGORY:
            if not any(s.enabled for s in self.categories.values()):
                raise ValueError("a category league needs at least one enabled category")
            if self.point_values:
                raise ValueError("point_values must be empty in a category league")
        else:  # POINTS
            if not self.point_values:
                raise ValueError("a points league needs a non-empty point_values map")
            if self.categories:
                raise ValueError("categories must be empty in a points league")
        return self

    @property
    def active_categories(self) -> list[StatCategory]:
        """Categories the value engine should score (enabled, not punted)."""
        return [c for c, s in self.categories.items() if s.enabled]

    @property
    def punted_categories(self) -> list[StatCategory]:
        return [c for c, s in self.categories.items() if s.is_punt]

    @classmethod
    def load(cls, path: str | Path) -> "LeagueConfig":
        """Load and validate a league config from a .json, .yaml, or .yml file.

        Supporting a new league means writing a file here, not editing engine
        code -- see leagues/ for worked examples to copy and edit.
        """
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix not in (".json", ".yaml", ".yml"):
            raise ValueError(
                f"Unsupported league config extension {suffix!r} for {path} "
                "-- use .json, .yaml, or .yml"
            )
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as e:
            raise FileNotFoundError(
                f"No league config at {path} -- see leagues/ for worked "
                "examples to copy and edit."
            ) from e
        data = json.loads(text) if suffix == ".json" else _require_yaml().safe_load(text)
        return cls.model_validate(data)

    def to_json(self, path: str | Path | None = None, *, indent: int = 2) -> str:
        """Serialize to a JSON string, optionally writing it to `path`."""
        text = self.model_dump_json(indent=indent)
        if path is not None:
            Path(path).write_text(text, encoding="utf-8")
        return text

    def save(self, path: str | Path) -> None:
        """Save to `path`; format is chosen by extension (.json, .yaml, .yml)."""
        path = Path(path)
        suffix = path.suffix.lower()
        if suffix == ".json":
            self.to_json(path)
        elif suffix in (".yaml", ".yml"):
            data = self.model_dump(mode="json")
            path.write_text(_require_yaml().safe_dump(data, sort_keys=False), encoding="utf-8")
        else:
            raise ValueError(
                f"Unsupported league config extension {suffix!r} for {path} "
                "-- use .json, .yaml, or .yml"
            )


# --------------------------------------------------------------------------- #
# Example configs -- these double as the "adaptivity proven" test from SCOPE.md.
# Feeding these three to the same engine should produce three different boards.
# --------------------------------------------------------------------------- #

def standard_9cat() -> LeagueConfig:
    return LeagueConfig(
        name="Standard 9-Cat",
        scoring_type=ScoringType.CATEGORY,
        num_teams=12,
        categories={
            cat: CategorySetting()
            for cat in (
                StatCategory.PTS, StatCategory.REB, StatCategory.AST,
                StatCategory.STL, StatCategory.BLK, StatCategory.FG3M,
                StatCategory.TOV, StatCategory.FG_PCT, StatCategory.FT_PCT,
            )
        },
        roster=[
            RosterSlot(position="PG", count=1), RosterSlot(position="SG", count=1),
            RosterSlot(position="SF", count=1), RosterSlot(position="PF", count=1),
            RosterSlot(position="C", count=1),  RosterSlot(position="G", count=1),
            RosterSlot(position="F", count=1),  RosterSlot(position="UTIL", count=2),
            RosterSlot(position="BENCH", count=3),
        ],
    )


def punt_ft_9cat() -> LeagueConfig:
    """Same as standard 9-cat, but punting FT% -- should promote low-FT% bigs."""
    cfg = standard_9cat()
    cfg.name = "9-Cat, Punt FT%"
    cfg.categories[StatCategory.FT_PCT] = CategorySetting(is_punt=True)
    return cfg


def points_league() -> LeagueConfig:
    # EXAMPLE coefficients only -- points leagues vary widely, so edit to match
    # your actual league. This is deliberately a common-ish default, not canon.
    return LeagueConfig(
        name="Example Points League",
        scoring_type=ScoringType.POINTS,
        num_teams=10,
        point_values={
            "pts": 1.0, "reb": 1.2, "ast": 1.5,
            "stl": 3.0, "blk": 3.0, "tov": -1.0, "fg3m": 0.5,
        },
        roster=[
            RosterSlot(position="PG", count=1), RosterSlot(position="SG", count=1),
            RosterSlot(position="SF", count=1), RosterSlot(position="PF", count=1),
            RosterSlot(position="C", count=1),  RosterSlot(position="UTIL", count=3),
            RosterSlot(position="BENCH", count=3),
        ],
    )


if __name__ == "__main__":
    for cfg in (standard_9cat(), punt_ft_9cat(), points_league()):
        print(f"\n{cfg.name}  [{cfg.scoring_type.value}, {cfg.num_teams} teams]")
        if cfg.scoring_type is ScoringType.CATEGORY:
            print("  scored:", [c.value for c in cfg.active_categories])
            if cfg.punted_categories:
                print("  punted:", [c.value for c in cfg.punted_categories])
        else:
            print("  points:", cfg.point_values)
