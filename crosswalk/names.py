"""
crosswalk/names.py -- player-name normalization and the manual override map.

This is the join layer: the same NBA player is spelled differently by nba_api,
Sleeper, and DARKO (accents, hyphens, generational suffixes, initials), and this
module turns any of those spellings into one stable match key, then resolves that
key to a canonical nba_id -- consulting `overrides.json` first for the handful of
players automatic matching can't get right.

Started life as the M0 de-risking spike (prove the join is even possible before
building on it); `run()` is what's left of that -- a printed nba_api vs Sleeper
match report, kept as a diagnostic. Everything else here is meant to be imported.

Sources `run()` touches:
  - nba_api  (numeric NBA person IDs + names)  -- the STATIC player list bundled
             with the package, so NO network call and no rate limit.
  - Sleeper  (Sleeper player IDs + names)      -- one cached HTTP GET.

DARKO is not wired in yet -- its data access needs its own step. The loader
pattern below (`load_*_players` -> {normalized_key: [{id, name}, ...]}) is what a
`load_darko_players()` slots into next.

Run from the repo root:
    python crosswalk/names.py             # print the match report
    python crosswalk/names.py --selftest  # run tests/test_crosswalk.py (no network)

Heavy imports (nba_api, requests) are lazy -- inside the loader functions -- so
normalization, override loading, and resolution can be imported and unit-tested
without them.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = str(_HERE.parent)
if _ROOT not in sys.path:  # allow `python crosswalk/names.py` to find util/, etc.
    sys.path.insert(0, _ROOT)

OVERRIDES_PATH = _HERE / "overrides.json"


# --------------------------------------------------------------------------- #
# The core of the whole crosswalk: turn a display name into a stable match key.
# --------------------------------------------------------------------------- #

# Generational suffixes are dropped so "Jaren Jackson Jr." matches "Jaren Jackson".
# Risk: two DIFFERENT active players identical but for a suffix would collide onto
# one key. run() scans the live pool for exactly that; overrides.json is the fix
# when it happens.
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str) -> str:
    """
    Lowercase, strip accents, drop punctuation and generational suffixes,
    and concatenate. "Luka Dončić" -> "lukadoncic".

    Pure and dependency-free on purpose -- callers outside the crosswalk
    (engine/diagnose.py) use it as a plain accent/case-insensitive key. The
    override map is applied by resolve(), not here.
    """
    # Decompose accented chars (é -> e + combining accent) and drop the accents.
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    # Split on anything that isn't a letter/number, lowercased.
    tokens = re.split(r"[^a-z0-9]+", ascii_only.lower())
    tokens = [t for t in tokens if t and t not in SUFFIXES]
    return "".join(tokens)


# --------------------------------------------------------------------------- #
# Manual override map: {normalized_key -> canonical nba_id}
# --------------------------------------------------------------------------- #

def load_overrides(path: str | os.PathLike = OVERRIDES_PATH) -> dict[str, int]:
    """Read overrides.json -> {normalized_key: nba_id}. A missing file is not an
    error (returns {}): the map only exists to hold hand-made exceptions and is
    empty until one is needed. Keys starting with '_' are metadata (_comment,
    _notes) and are skipped. Values must be ints (nba person ids)."""
    p = Path(path)
    if not p.exists():
        return {}
    raw = json.loads(p.read_text(encoding="utf-8"))
    entries = raw.get("overrides", raw) if isinstance(raw, dict) else {}
    out: dict[str, int] = {}
    for key, value in entries.items():
        if key.startswith("_"):
            continue
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(
                f"overrides.json: {key!r} -> {value!r} is not an integer nba_id"
            )
        out[normalize_name(key)] = value
    return out


def collisions(key_map: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Every normalized key that more than one player maps to -- the suffix
    collisions (and any genuine namesakes) that automatic matching cannot
    resolve. Empty is the expected, healthy result."""
    return {k: v for k, v in key_map.items() if len(v) > 1}


def resolve(
    name: str,
    key_map: dict[str, list[dict]],
    overrides: dict[str, int] | None = None,
) -> tuple[int | None, str]:
    """Resolve a display name to a canonical nba_id against one source's
    key_map. Returns (id_or_None, status):

      "override"  -- overrides.json pinned this key to an id
      "ok"        -- exactly one player under this key
      "collision" -- two or more players share this key; needs an override
      "miss"      -- no player under this key in this source
    """
    key = normalize_name(name)
    if overrides is None:
        overrides = load_overrides()
    if key in overrides:
        return overrides[key], "override"
    hits = key_map.get(key) or []
    if len(hits) == 1:
        return hits[0]["id"], "ok"
    if len(hits) > 1:
        return None, "collision"
    return None, "miss"


# A stress-test set chosen to break naive matching: accents, hyphens, suffixes,
# apostrophes, initials, plus a near-duplicate pair (Bogdan vs Bojan Bogdanovic)
# to confirm the normalizer does NOT wrongly merge distinct players.
TEST_PLAYERS = [
    "Luka Dončić", "Nikola Jokić", "Giannis Antetokounmpo", "Shai Gilgeous-Alexander",
    "Karl-Anthony Towns", "Jaren Jackson Jr.", "Michael Porter Jr.", "De'Aaron Fox",
    "Dennis Schröder", "Alperen Şengün", "Nikola Vučević", "Bogdan Bogdanović",
    "Bojan Bogdanović", "P.J. Washington", "OG Anunoby", "Victor Wembanyama",
    "LeBron James", "Stephen Curry", "Jayson Tatum", "Domantas Sabonis",
]


# --------------------------------------------------------------------------- #
# Loaders: each returns {normalized_key: [ {id, name}, ... ]}
# (a list per key so we can detect collisions where two players share a key)
# --------------------------------------------------------------------------- #

def load_nba_players() -> dict[str, list[dict]]:
    from nba_api.stats.static import players as nba_players  # lazy import

    key_map: dict[str, list[dict]] = {}
    for p in nba_players.get_active_players():
        key = normalize_name(p["full_name"])
        key_map.setdefault(key, []).append({"id": p["id"], "name": p["full_name"]})
    return key_map


def _build_sleeper_map(raw: dict) -> dict[str, list[dict]]:
    key_map: dict[str, list[dict]] = {}
    for pid, rec in raw.items():
        if not isinstance(rec, dict):
            continue
        full = rec.get("full_name") or " ".join(
            t for t in (rec.get("first_name"), rec.get("last_name")) if t
        )
        if not full.strip():
            continue
        key = normalize_name(full)
        key_map.setdefault(key, []).append({"id": pid, "name": full})
    return key_map


def load_sleeper_players(
    cache_path: str = "data/sleeper_nba_players.json",
    max_age_hours: float = 24.0,
) -> tuple[dict[str, list[dict]], str]:
    import requests  # lazy import

    # Sleeper asks callers not to hit this endpoint more than once a day, so cache.
    if os.path.exists(cache_path):
        age_hours = (time.time() - os.path.getmtime(cache_path)) / 3600
        if age_hours < max_age_hours:
            with open(cache_path, "r", encoding="utf-8") as f:
                return _build_sleeper_map(json.load(f)), "cache"

    url = "https://api.sleeper.app/v1/players/nba"
    resp = requests.get(url, timeout=60, headers={"User-Agent": "fantasy-engine-spike/0.1"})
    resp.raise_for_status()
    raw = resp.json()

    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(raw, f)
    return _build_sleeper_map(raw), "network"


# --------------------------------------------------------------------------- #
# The match report (formerly the spike)
# --------------------------------------------------------------------------- #

def _fmt(hits: list[dict] | None) -> str:
    if not hits:
        return "MISS"
    if len(hits) > 1:
        return "COLLISION(" + ", ".join(str(h["id"]) for h in hits) + ")"
    return str(hits[0]["id"])


def _report_collisions(key_map: dict[str, list[dict]]) -> None:
    """Loudly surface any normalized key shared by 2+ active players. This is the
    exact failure mode overrides.json exists for; without this scan nobody knows
    whether the current pool actually contains one."""
    found = collisions(key_map)
    print("Suffix-collision scan (keys shared by 2+ active players):")
    if not found:
        print("  none -- the current active-player pool has no colliding keys.\n")
        return
    print(f"  {len(found)} colliding key(s) -- add each to crosswalk/overrides.json:")
    for key, hits in sorted(found.items()):
        who = ", ".join(f"{h['name']} ({h['id']})" for h in hits)
        print(f"    {key:<24} {who}")
    print()


def run() -> None:
    from util.console import configure_stdout_utf8  # player names carry non-cp1252 chars

    configure_stdout_utf8()
    print("Loading nba_api static players...")
    nba_map = load_nba_players()
    print(f"  {sum(len(v) for v in nba_map.values())} active players\n")

    _report_collisions(nba_map)

    overrides = load_overrides()
    print(f"Loaded {len(overrides)} override(s) from {OVERRIDES_PATH.name}\n")

    print("Loading Sleeper players (first run hits the network, then caches)...")
    try:
        sleeper_map, src = load_sleeper_players()
        print(f"  loaded from {src}: {sum(len(v) for v in sleeper_map.values())} players\n")
    except Exception as e:  # noqa: BLE001 -- diagnostic: report and continue
        print(f"  Sleeper load FAILED: {e}\n  (reporting nba_api column only)\n")
        sleeper_map = {}

    print(f"{'player':<26} {'nba_api':<14} {'sleeper':<14} status")
    print("-" * 66)

    stragglers = []
    for name in TEST_PLAYERS:
        key = normalize_name(name)
        nba_hit = nba_map.get(key)
        sl_hit = sleeper_map.get(key)
        nba_id, nba_status = resolve(name, nba_map, overrides)
        ok = nba_id is not None and (bool(sl_hit) or not sleeper_map)
        status = f"ok ({nba_status})" if ok and nba_status == "override" else \
                 "ok" if ok else "NEEDS OVERRIDE"
        if not ok:
            stragglers.append(name)
        shown = str(nba_id) if nba_status == "override" else _fmt(nba_hit)
        print(f"{name:<26} {shown:<14} {_fmt(sl_hit):<14} {status}")

    print("-" * 66)
    total = len(TEST_PLAYERS)
    print(f"\nMatched cleanly: {total - len(stragglers)}/{total}")
    if stragglers:
        print("Stragglers (add to crosswalk/overrides.json): " + ", ".join(stragglers))
        print(
            "\nThat's the expected, useful output -- these are the names whose\n"
            "spelling differs across sources. The fix is a small hand-written\n"
            "{normalized_key -> canonical_id} override, not more clever matching."
        )
    else:
        print("No stragglers in this set -- normalization handled every case.")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        # Thin wrapper: runs tests/test_crosswalk.py under pytest (no network,
        # no nba_api needed -- pure name-normalization / override checks).
        import pytest

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rc = pytest.main(["-q", os.path.join(root, "tests", "test_crosswalk.py")])
        if rc != 0:
            raise SystemExit(rc)
        print("selftest ok: see tests/test_crosswalk.py")
    else:
        run()
