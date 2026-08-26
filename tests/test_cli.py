"""tests/test_cli.py -- B2: cli/board.py, the M4 front door.

All offline: cli.board._load_players is monkeypatched to a fixed synthetic
dataset so nothing here touches ingest/nba_api. LeagueConfig files loaded
below (leagues/*.json) are real committed repo files, not network data.
"""
from __future__ import annotations

import csv

import pytest

from cli.board import (
    _CSV_FIELDS,
    _board_rows,
    _normalize_basis,
    _print_table,
    _write_csv,
    build_parser,
    main,
)
from engine.league_config import standard_9cat
from engine.value import compute_values
from tests.fixtures import SIX_PLAYER_FIXTURE

ACCENTED_FIXTURE = dict(SIX_PLAYER_FIXTURE)
ACCENTED_FIXTURE[99] = dict(
    name="Luka Dončić", gp=70, pts=25, reb=8, ast=9, stl=1.2, blk=0.5,
    tov=3.0, fg3m=3.0, fg_pct=0.5, fga=20, ft_pct=0.78, fta=8,
)


# --- argument parsing ---

def test_basis_total_alias_accepted_by_parser():
    args = build_parser().parse_args(["--basis", "total", "--league", "x.json"])
    assert args.basis == "total"


def test_normalize_basis_maps_total_to_availability_adjusted():
    assert _normalize_basis("total") == "availability_adjusted"
    assert _normalize_basis("per_game") == "per_game"
    assert _normalize_basis("availability_adjusted") == "availability_adjusted"


def test_basis_unknown_value_rejected():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["--basis", "bogus", "--league", "x.json"])


def test_league_required_unless_compare(capsys):
    rc = main([])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.err.startswith("Error:")


def test_explain_and_compare_mutually_exclusive(capsys):
    rc = main(["--explain", "x", "--compare", "leagues/standard_9cat.json", "leagues/punt_ft.json"])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.err.startswith("Error:")


# --- _board_rows / CSV: shape and content ---

def test_board_rows_covers_all_eligible_players_sorted_by_rank():
    rows = _board_rows(SIX_PLAYER_FIXTURE, standard_9cat(), "availability_adjusted")
    assert len(rows) == 6
    assert [r["rank"] for r in rows] == list(range(1, 7))
    expected_keys = {"rank", "name", "nba_id", "position", "value", "vor",
                      "availability_adjusted_rank", "per_game_rank", "rank_delta"}
    assert set(rows[0].keys()) == expected_keys


def test_board_rows_rank_delta_matches_independently_computed_boards():
    rows = _board_rows(SIX_PLAYER_FIXTURE, standard_9cat(), "availability_adjusted")
    adj_rank = {r["nba_id"]: r["rank"]
                for r in compute_values(SIX_PLAYER_FIXTURE, standard_9cat(), basis="availability_adjusted")}
    pg_rank = {r["nba_id"]: r["rank"]
               for r in compute_values(SIX_PLAYER_FIXTURE, standard_9cat(), basis="per_game")}
    for row in rows:
        assert row["availability_adjusted_rank"] == adj_rank[row["nba_id"]]
        assert row["per_game_rank"] == pg_rank[row["nba_id"]]
        assert row["rank_delta"] == adj_rank[row["nba_id"]] - pg_rank[row["nba_id"]]


def test_print_table_top_limits_printed_rows_but_not_the_board(capsys):
    rows = _board_rows(SIX_PLAYER_FIXTURE, standard_9cat(), "availability_adjusted")
    _print_table(rows, "Test League", "desc", "availability_adjusted", top=2)
    out = capsys.readouterr().out
    for row in rows[:2]:
        assert row["name"] in out
    for row in rows[2:]:
        assert row["name"] not in out
    assert "4 more" in out


def test_write_csv_writes_every_row_and_makes_parent_dirs(tmp_path):
    rows = _board_rows(SIX_PLAYER_FIXTURE, standard_9cat(), "availability_adjusted")
    path = tmp_path / "nested" / "board.csv"
    _write_csv(str(path), rows)
    with open(path, newline="", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
    assert len(reader) == 6
    assert set(reader[0].keys()) == set(_CSV_FIELDS)


def test_csv_gets_full_board_even_when_top_is_small(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr("cli.board._load_players", lambda args: (SIX_PLAYER_FIXTURE, "test"))
    csv_path = tmp_path / "board.csv"
    rc = main(["--league", "leagues/standard_9cat.json", "--top", "2", "--csv", str(csv_path)])
    assert rc == 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))
    assert len(reader) == 6  # not clipped to --top


# --- --explain routing ---

def test_explain_routes_through_diagnose_and_prints_breakdown(monkeypatch, capsys):
    monkeypatch.setattr("cli.board._load_players", lambda args: (SIX_PLAYER_FIXTURE, "test"))
    rc = main(["--league", "leagues/standard_9cat.json", "--explain", "allround"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "allround" in out
    assert "TOTAL" in out


@pytest.mark.parametrize("query", ["Luka Dončić", "Luka Doncic", "luka doncic", "LUKA DONCIC"])
def test_explain_accent_insensitive_routing(monkeypatch, capsys, query):
    monkeypatch.setattr("cli.board._load_players", lambda args: (ACCENTED_FIXTURE, "test"))
    rc = main(["--league", "leagues/standard_9cat.json", "--explain", query])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Luka Dončić" in out
    assert "not found" not in out


# --- --compare ---

def test_compare_promotes_low_ft_big(monkeypatch, capsys):
    monkeypatch.setattr("cli.board._load_players", lambda args: (SIX_PLAYER_FIXTURE, "test"))
    rc = main(["--compare", "leagues/standard_9cat.json", "leagues/punt_ft.json", "--basis", "per_game"])
    out = capsys.readouterr().out
    assert rc == 0
    risers_section = out.split("Biggest fallers")[0]
    assert "giannis_like" in risers_section


# --- error handling ---

def test_missing_league_file_returns_clean_error_not_traceback(monkeypatch, capsys):
    monkeypatch.setattr("cli.board._load_players", lambda args: (SIX_PLAYER_FIXTURE, "test"))
    rc = main(["--league", "leagues/does_not_exist.json"])
    captured = capsys.readouterr()
    assert rc == 1
    assert captured.err.startswith("Error:")
    assert "Traceback" not in captured.err
