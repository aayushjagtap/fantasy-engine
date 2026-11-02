"""Cache-only coverage audit for the shipped 2026-27 first round."""
from __future__ import annotations

import json
import os
import sys
import unicodedata

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine.projection import ROOKIES_PATH, load_rookies
from engine.rookie_baseline import load_comparator_baselines, primary_group
from ingest.nba_boxscores import get_season_boxscores
from ingest.nba_rosters import get_season_rosters


def _identity(name: str) -> str:
    name = name.replace("\ufffd", "o")  # the committed Karim López cache value lost ó
    value = unicodedata.normalize("NFKD", name.casefold())
    value = "".join(c for c in value if not unicodedata.combining(c) and c.isalnum())
    return value.replace("jr", "").replace("sr", "")


def main() -> int:
    with open(ROOKIES_PATH, encoding="utf-8") as f:
        doc = json.load(f)
    entries = doc.get("rookies", [])
    roster, coverage = get_season_rosters("2026-27", offline=True)
    baselines, meta = load_comparator_baselines()
    projections = load_rookies(verbose=False, target_season="2026-27")
    latest_stats, _source = get_season_boxscores("2025-26", offline=True)
    ids, names, picks = [e["nba_id"] for e in entries], [_identity(e["name"]) for e in entries], [e["draft_pick"] for e in entries]
    print(f"2026-27 first-round entries: {len(entries)}")
    print(f"Positive, unique NBA IDs present: {len(set(ids)) if all(x > 0 for x in ids) else 'INVALID'} / {len(ids)}")
    print(f"Draft-pick coverage: {len(set(picks))} unique slots ({min(picks, default=0)}-{max(picks, default=0)})")
    matches = sum(e["nba_id"] in roster for e in entries)
    name_matches = sum(e["nba_id"] in roster and
                       _identity(roster[e["nba_id"]].get("name") or "") == _identity(e["name"])
                       for e in entries)
    print(f"Current-roster matches: {matches}/{len(entries)}; normalized names: {name_matches}/{len(entries)}")
    for e in entries:
        cached = roster.get(e["nba_id"])
        if not cached or _identity(cached.get("name") or "") != _identity(e["name"]):
            print(f"  cache identity spelling issue: pick {e['draft_pick']} {e['name']} / "
                  f"{cached.get('name') if cached else 'no roster row'}")
    print("Comparator sample sizes:")
    for group in ("G", "F", "C"):
        b = baselines[group]
        print(f"  {group}: {b['sample_size']} ({'supported' if b['supported'] else 'below minimum 20'})")
    print(f"Comparator caches: {', '.join(meta['seasons'])}")
    print(f"Position proxy: {meta['roster_proxy']}; multi-position precedence C > F > G")
    print(f"Supported projections: {len(projections)}")
    unsupported = [e for e in entries if e["nba_id"] not in projections]
    print(f"Unsupported projections: {len(unsupported)}")
    for e in unsupported:
        group = e.get("baseline_position") or primary_group(e.get("position", ()))
        sample = baselines.get(group, {}).get("sample_size", 0)
        why = "no defensible position" if not group else f"{group} baseline has {sample} observations; requires 20"
        print(f"  pick {e['draft_pick']} {e['name']} ({group or '?'}): {why}")
    duplicate_ids = len(ids) - len(set(ids))
    duplicate_names = len(names) - len(set(names))
    print(f"Potential duplicate IDs: {duplicate_ids}; normalized names: {duplicate_names}")
    expected = sum(e["nba_id"] in roster and e["nba_id"] in projections and
                   e["nba_id"] not in latest_stats for e in entries)
    print(f"Expected board additions (supported, rostered, no 2025-26 NBA line): {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
