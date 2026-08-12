"""
crosswalk/spike.py -- M0 de-risking spike.

Goal: prove that the same NBA player can be reliably matched across data sources
that each spell names differently, and surface the handful that CAN'T be matched
automatically (the "stragglers" that need a manual override map).

Sources in this spike:
  - nba_api  (numeric NBA person IDs + names)  -- uses the STATIC player list,
             which is bundled with the package, so NO network call and no rate limit.
  - Sleeper  (Sleeper player IDs + names)      -- one cached HTTP GET.

DARKO is deliberately NOT here yet -- its data access needs its own step. The
loader pattern below is what a `load_darko_players()` will slot into next.

Run from the repo root:
    python crosswalk/spike.py

Heavy imports (nba_api, requests) are lazy -- inside the loader functions -- so
the normalization logic can be imported and unit-tested without them.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import unicodedata


# --------------------------------------------------------------------------- #
# The core of the whole crosswalk: turn a display name into a stable match key.
# --------------------------------------------------------------------------- #

# Generational suffixes are dropped so "Jaren Jackson Jr." matches "Jaren Jackson".
# Risk: two DIFFERENT active players identical but for a suffix would collide.
# That's rare, and the manual-override map is the escape hatch when it happens.
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def normalize_name(name: str) -> str:
    """
    Lowercase, strip accents, drop punctuation and generational suffixes,
    and concatenate. "Luka Dončić" -> "lukadoncic".
    """
    # Decompose accented chars (é -> e + combining accent) and drop the accents.
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    # Split on anything that isn't a letter/number, lowercased.
    tokens = re.split(r"[^a-z0-9]+", ascii_only.lower())
    tokens = [t for t in tokens if t and t not in SUFFIXES]
    return "".join(tokens)


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
# The spike itself
# --------------------------------------------------------------------------- #

def _fmt(hits: list[dict] | None) -> str:
    if not hits:
        return "MISS"
    if len(hits) > 1:
        return "COLLISION(" + ", ".join(str(h["id"]) for h in hits) + ")"
    return str(hits[0]["id"])


def run() -> None:
    print("Loading nba_api static players...")
    nba_map = load_nba_players()
    print(f"  {sum(len(v) for v in nba_map.values())} active players\n")

    print("Loading Sleeper players (first run hits the network, then caches)...")
    try:
        sleeper_map, src = load_sleeper_players()
        print(f"  loaded from {src}: {sum(len(v) for v in sleeper_map.values())} players\n")
    except Exception as e:  # noqa: BLE001 -- spike: report and continue
        print(f"  Sleeper load FAILED: {e}\n  (reporting nba_api column only)\n")
        sleeper_map = {}

    print(f"{'player':<26} {'nba_api':<14} {'sleeper':<14} status")
    print("-" * 66)

    stragglers = []
    for name in TEST_PLAYERS:
        key = normalize_name(name)
        nba_hit = nba_map.get(key)
        sl_hit = sleeper_map.get(key)
        ok = bool(nba_hit) and (bool(sl_hit) or not sleeper_map)
        status = "ok" if ok else "NEEDS OVERRIDE"
        if not ok:
            stragglers.append(name)
        print(f"{name:<26} {_fmt(nba_hit):<14} {_fmt(sl_hit):<14} {status}")

    print("-" * 66)
    total = len(TEST_PLAYERS)
    print(f"\nMatched cleanly: {total - len(stragglers)}/{total}")
    if stragglers:
        print("Stragglers (add to a manual override map): " + ", ".join(stragglers))
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
        # no nba_api needed -- pure name-normalization checks).
        import pytest

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        rc = pytest.main(["-q", os.path.join(root, "tests", "test_crosswalk.py")])
        if rc != 0:
            raise SystemExit(rc)
        print("selftest ok: see tests/test_crosswalk.py")
    else:
        run()
