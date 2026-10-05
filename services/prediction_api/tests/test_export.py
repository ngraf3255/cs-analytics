"""Tableau CSV export: GET /matches/export/{rounds,matches}.csv and python -m steamlink.export.
Runs on SQLite by default and on PostgreSQL with CSA_TEST_DATABASE_URL."""

import csv
import io
import re
from datetime import datetime, timezone

import pytest

import main
from steamlink import export
from steamlink.demo_parser import DemoParser, ParsedDeath, ParsedDemo, ParsedRound, ParsedSpawn
from steamlink.storage.base import UNKNOWN_MATCH_ID, UPLOAD_KEY_PREFIX, NewMatch
from steamlink.upload import import_uploaded_demo

from test_api_steam import make_client, login
from test_personal import ME, MIRAGE_PLAYERS, MIRAGE_ROUNDS, NUKE_PLAYERS, NUKE_ROUNDS, THEM, rnd, store
from test_summary import session

ROUNDS_URL, MATCHES_URL = "/matches/export/rounds.csv", "/matches/export/matches.csv"
YOU_ROUND_COLUMNS = [c for c in export.ROUND_COLUMNS if c.startswith(("you_", "opponent_"))]
YOU_MATCH_COLUMNS = [c for c in export.MATCH_COLUMNS if c.startswith(("you_", "opponent_"))]


def read(response):
    assert response.status_code == 200, response.text
    text = response.text
    header = next(csv.reader(io.StringIO(text)))
    return header, list(csv.DictReader(io.StringIO(text)))


@pytest.fixture()
def app_client(tmp_path):
    client, ctx = make_client(tmp_path)
    login(client, ctx)
    return client, ctx


def test_requires_sign_in_and_is_not_shadowed_by_match_id(tmp_path):
    client, ctx = make_client(tmp_path)
    for url in (ROUNDS_URL, MATCHES_URL):
        assert client.get(url).status_code == 401
    login(client, ctx)
    assert client.get("/matches/export/players.csv").json()["detail"] == "export_not_found"
    assert client.get("/matches/export/rounds.json").status_code == 404
    response = client.get(ROUNDS_URL)
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"].startswith('attachment; filename="cs2-rounds-')
    assert response.headers["cache-control"] == "no-store"


def test_header_only_without_matches(app_client):
    client, _ = app_client
    header, rows = read(client.get(ROUNDS_URL))
    assert header == list(export.ROUND_COLUMNS) and rows == []
    header, rows = read(client.get(MATCHES_URL))
    assert header == list(export.MATCH_COLUMNS) and rows == []


def test_stable_column_names():
    """Renaming / reordering breaks saved Tableau workbooks: only append (bump EXPORT_VERSION on meaning changes)."""

    assert export.ROUND_COLUMNS[:13] == (
        "match_id", "map_name", "match_date", "match_day", "date_source", "played_at", "imported_at", "source",
        "round_number", "winner_side", "ct_won", "opening_kill_side", "opening_kill_seconds")
    assert len(set(export.ROUND_COLUMNS)) == len(export.ROUND_COLUMNS)
    assert len(set(export.MATCH_COLUMNS)) == len(export.MATCH_COLUMNS)
    assert {"model_ct_win_probability", "model_correct", "you_side", "you_kills", "you_deaths",
            "you_opening_kill", "you_opening_death", "match_score_ct"} <= set(export.ROUND_COLUMNS)


def test_rounds_with_the_users_side_model_and_running_score(app_client):
    client, ctx = app_client
    played = datetime(2026, 9, 30, 19, 5, tzinfo=timezone.utc)
    mirage = store(ctx, ME, "m1", "de_mirage", MIRAGE_ROUNDS, MIRAGE_PLAYERS, score=(3, 1), played_at=played)
    nuke = store(ctx, ME, "n1", "de_nuke", NUKE_ROUNDS, NUKE_PLAYERS, score=(1, 2))
    header, rows = read(client.get(ROUNDS_URL))
    assert header == list(export.ROUND_COLUMNS)
    # newest first (nuke added after mirage was played), rounds in order
    assert [(r["match_id"], r["round_number"]) for r in rows] == \
        [(nuke, "1"), (nuke, "2"), (nuke, "3")] + [(mirage, str(n)) for n in range(1, 5)]
    m = [r for r in rows if r["match_id"] == mirage]
    assert m[0]["match_date"] == "2026-09-30T19:05:00Z" and m[0]["date_source"] == "played"
    assert m[0]["match_day"] == "2026-09-30"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", rows[0]["imported_at"])
    assert m[0]["played_at"] == "2026-09-30T19:05:00Z" and m[0]["imported_at"].endswith("Z")
    assert rows[0]["date_source"] == "imported" and rows[0]["played_at"] == ""
    assert rows[0]["match_date"] == rows[0]["imported_at"]
    assert {r["source"] for r in rows} == {"upload"} and {r["map_name"] for r in m} == {"de_mirage"}
    # ME: T, T, CT, CT; winners t, ct, ct, ct -> won, lost, won, won; running score 1-0, 1-1, 2-1, 3-1
    assert [(r["winner_side"], r["ct_won"], r["you_side"], r["you_won"]) for r in m] == [
        ("t", "0", "t", "1"), ("ct", "1", "t", "0"), ("ct", "1", "ct", "1"), ("ct", "1", "ct", "1")]
    assert [(r["you_score_after"], r["opponent_score_after"]) for r in m] == [("1", "0"), ("1", "1"), ("2", "1"), ("3", "1")]
    assert [(r["you_kills"], r["you_deaths"], r["you_opening_kill"], r["you_opening_death"], r["you_survived"])
            for r in m] == [("2", "0", "1", "0", "1"), ("0", "1", "0", "1", "0"), ("1", "1", "0", "0", "0"),
                            ("3", "0", "0", "0", "1")]
    assert {(r["match_score_ct"], r["match_score_t"], r["needs_reupload"]) for r in m} == {("3", "1", "0")}
    # model columns = the match report's per-round prediction
    scores = main.scorer.score_rounds("de_mirage", MIRAGE_ROUNDS)
    for row, score, record in zip(m, scores, MIRAGE_ROUNDS):
        assert float(row["model_ct_win_probability"]) == pytest.approx(score.probability_ct, abs=1e-6)
        assert row["model_predicted_winner"] == score.predicted_winner
        assert row["model_correct"] == ("1" if score.predicted_winner == record.winner_side else "0")
        mine = score.probability_ct if row["you_side"] == "ct" else score.probability_t
        assert float(row["you_win_probability"]) == pytest.approx(mine, abs=1e-6)
        assert row["opening_kill_side"] == "t" and row["opening_weapon"] == "ak47"
        assert row["opening_kill_seconds"] == "20.0" and row["unscored_reason"] == ""
        assert row["opening_kill_side_won"] == ("1" if record.winner_side == "t" else "0")


def test_matches_csv_summary_metrics(app_client):
    client, ctx = app_client
    mirage = store(ctx, ME, "m1", "de_mirage", MIRAGE_ROUNDS, MIRAGE_PLAYERS, score=(3, 1))
    header, rows = read(client.get(MATCHES_URL))
    assert header == list(export.MATCH_COLUMNS)
    (row,) = rows
    report = client.get(f"/matches/{mirage}").json()
    assert row["match_id"] == mirage and row["status"] == "imported" and row["rounds"] == "4"
    assert (row["rounds_with_winner"], row["ct_rounds_won"], row["t_rounds_won"], row["ct_round_win_rate"]) == \
        ("4", "3", "1", "0.75")
    assert (row["opening_kill_rounds"], row["opening_kill_converted"], row["opening_kill_conversion_rate"]) == \
        ("4", "1", "0.25")
    assert row["model_scored_rounds"] == str(report["summary"]["scored"])
    assert row["model_correct"] == str(report["summary"]["correct_predictions"])
    assert row["model_brier_score"] != ""
    you = report["you"]
    assert (row["you_status"], row["you_first_side"], row["you_last_side"]) == ("in_match", "t", "ct")
    assert (row["you_rounds"], row["you_rounds_won"], row["you_round_win_rate"]) == ("4", "3", "0.75")
    assert (row["you_kills"], row["you_deaths"], row["you_kd"]) == (str(you["kills"]), str(you["deaths"]), str(you["kd"]))
    assert (row["you_opening_kills"], row["you_opening_deaths"], row["you_survived"]) == ("1", "1", "2")
    assert (row["you_score"], row["opponent_score"], row["you_result"]) == ("3", "1", "won")


def test_personal_columns_blank_when_the_user_is_not_in_the_match(app_client):
    client, ctx = app_client
    pro = store(ctx, ME, "pro", "de_nuke", NUKE_ROUNDS, ())  # players recorded, ME not among them
    old = store(ctx, ME, "old", "de_mirage", MIRAGE_ROUNDS, recorded=False)  # parsed before players were recorded
    _, rows = read(client.get(ROUNDS_URL))
    assert len(rows) == 3 + 4
    for row in rows:
        assert all(row[c] == "" for c in YOU_ROUND_COLUMNS), row
    assert {r["needs_reupload"] for r in rows if r["match_id"] == old} == {"1"}
    assert {r["needs_reupload"] for r in rows if r["match_id"] == pro} == {"0"}
    _, matches = read(client.get(MATCHES_URL))
    by_id = {m["match_id"]: m for m in matches}
    assert by_id[pro]["you_status"] == "not_in_match" and by_id[old]["you_status"] == "unknown"
    assert by_id[old]["outdated_reason"] == "players_not_recorded"
    for m in matches:
        assert all(m[c] == "" for c in YOU_MATCH_COLUMNS if c != "you_status"), m


def test_only_the_users_own_and_shared_matches(app_client):
    client, ctx = app_client
    mine = store(ctx, ME, "a", "de_mirage", MIRAGE_ROUNDS, MIRAGE_PLAYERS)
    theirs = store(ctx, THEM, "b", "de_nuke", NUKE_ROUNDS)
    shared = store(ctx, THEM, "c", "de_mirage", MIRAGE_ROUNDS, MIRAGE_PLAYERS)
    assert store(ctx, ME, "c", "de_mirage", MIRAGE_ROUNDS, MIRAGE_PLAYERS) == shared  # same demo: shared row
    _, rows = read(client.get(ROUNDS_URL))
    assert {r["match_id"] for r in rows} == {mine, shared}
    _, matches = read(client.get(MATCHES_URL))
    assert {m["match_id"] for m in matches} == {mine, shared}
    # The other player sees theirs + the shared match, with THEIR side in the you_* columns.
    other = session(client, ctx, THEM)
    _, rows = read(other.get(ROUNDS_URL))
    assert {r["match_id"] for r in rows} == {theirs, shared}
    assert [r["you_side"] for r in rows if r["match_id"] == shared] == ["ct", "ct", "t", "t"]
    _, matches = read(other.get(MATCHES_URL))
    assert {m["match_id"] for m in matches} == {theirs, shared}


def test_stub_matches_only_in_matches_csv(app_client):
    client, ctx = app_client
    owner = ctx.storage.get_or_create_user(ME, ctx.clock())
    stub = NewMatch(share_code=UPLOAD_KEY_PREFIX + "x", valve_match_id=UNKNOWN_MATCH_ID, status="unavailable",
                    status_reason="demo_unavailable", map_name=None, source="upload", parse_version=0)
    stub_id, _ = ctx.storage.record_uploaded_match(owner.id, match=stub, now=ctx.clock())
    _, rows = read(client.get(ROUNDS_URL))
    assert rows == []
    _, (row,) = read(client.get(MATCHES_URL))
    assert (row["match_id"], row["status"], row["status_reason"], row["rounds"]) == \
        (stub_id, "unavailable", "demo_unavailable", "0")
    assert row["you_status"] == "" and row["needs_reupload"] == "0"


class KnifeRoundParser(DemoParser):
    """A demo with a knife round, the match restart (begin_new_match) and 2 match rounds."""

    def parse(self, demo_path):
        return ParsedDemo(
            map_name="de_mirage",
            rounds=[ParsedRound(1, 100, 900, "t"), ParsedRound(2, 1500, 3000, "ct"), ParsedRound(3, 3200, 4000, "t")],
            deaths=[ParsedDeath(500, "t", "ct", "knife", 0, 0, ME, THEM),  # knife round
                    ParsedDeath(1100, "t", "ct", "ak47", 0, 0, ME, THEM),  # warmup after it
                    ParsedDeath(1600, "ct", "t", "m4a1", 0, 0, THEM, ME),
                    ParsedDeath(3500, "t", "ct", "ak47", 0, 1, ME, THEM)],
            spawns=[ParsedSpawn(50, ME, "t"), ParsedSpawn(50, THEM, "ct"), ParsedSpawn(1450, ME, "t"),
                    ParsedSpawn(1450, THEM, "ct"), ParsedSpawn(3150, ME, "t"), ParsedSpawn(3150, THEM, "ct")],
            match_start_tick=1000,
        )


def test_warmup_and_knife_rounds_are_not_exported(app_client, tmp_path):
    client, ctx = app_client
    raw = tmp_path / "knife.dem"
    raw.write_bytes(b"PBDEMS2\x00" + b"\x00" * 64)
    user = ctx.storage.get_or_create_user(ME, ctx.clock())
    import_uploaded_demo(storage=ctx.storage, parser=KnifeRoundParser(), user_id=user.id, raw_path=str(raw),
                         workdir=str(tmp_path), max_compressed_bytes=10**6, max_demo_bytes=10**6, now=ctx.clock())
    _, rows = read(client.get(ROUNDS_URL))
    assert [(r["round_number"], r["winner_side"], r["opening_weapon"]) for r in rows] == \
        [("1", "ct", "m4a1"), ("2", "t", "ak47")]
    assert "knife" not in {r["opening_weapon"] for r in rows}
    assert [(r["you_side"], r["you_kills"], r["you_deaths"]) for r in rows] == [("t", "0", "1"), ("t", "1", "0")]
    _, (match,) = read(client.get(MATCHES_URL))
    assert (match["rounds"], match["match_score_ct"], match["match_score_t"]) == ("2", "1", "1")


def test_text_that_looks_like_a_formula_is_neutralised(app_client):
    client, ctx = app_client
    store(ctx, ME, "f", "=HYPERLINK(\"x\")", [rnd(1, "ct", weapon="+cmd")])
    _, (row,) = read(client.get(ROUNDS_URL))
    assert row["map_name"] == "'=HYPERLINK(\"x\")" and row["opening_weapon"] == "'+cmd"
    assert export._cell(-1.5) == "-1.5" and export._cell(True) == "1" and export._cell(None) == ""


def test_offline_script_writes_the_same_tables(app_client, tmp_path, capsys):
    client, ctx = app_client
    store(ctx, ME, "m1", "de_mirage", MIRAGE_ROUNDS, MIRAGE_PLAYERS, score=(3, 1))
    store(ctx, THEM, "n1", "de_nuke", NUKE_ROUNDS, NUKE_PLAYERS)  # someone else's: not exported
    url = ctx.storage.engine.url.render_as_string(hide_password=False)
    out = tmp_path / "out"
    assert export.main(["--database-url", url, "--steam-id", ME, "--out-dir", str(out)]) == 0
    assert "rounds: 4 rows" in capsys.readouterr().out
    for table, api_url in (("rounds", ROUNDS_URL), ("matches", MATCHES_URL)):
        assert (out / f"app_{table}.csv").read_bytes() == client.get(api_url).content
    # --no-personal leaves the you_* columns empty; unknown Steam ID -> exit 2, nothing created
    assert export.main(["--database-url", url, "--steam-id", ME, "--out-dir", str(out), "--no-personal"]) == 0
    with open(out / "app_rounds.csv", newline="", encoding="utf-8") as fh:
        assert all(row[c] == "" for row in csv.DictReader(fh) for c in YOU_ROUND_COLUMNS)
    assert export.main(["--database-url", url, "--steam-id", "76561198999999999", "--out-dir", str(tmp_path / "x")]) == 2
    assert not (tmp_path / "x").exists()
    assert ctx.storage.find_user("76561198999999999") is None


def test_large_export_is_streamed_in_chunks():
    rows = ({"match_id": str(i)} for i in range(1201))
    chunks = list(export.csv_chunks(("match_id",), rows, batch=500))
    assert len(chunks) == 3 and "".join(chunks).count("\r\n") == 1202


def test_python_export_app_tableau_script(app_client, tmp_path):
    """python/export_app_tableau.py (repo root) wraps python -m steamlink.export."""

    import subprocess
    import sys
    from pathlib import Path

    client, ctx = app_client
    store(ctx, ME, "m1", "de_mirage", MIRAGE_ROUNDS, MIRAGE_PLAYERS, score=(3, 1))
    script = Path(__file__).resolve().parents[3] / "python" / "export_app_tableau.py"
    url = ctx.storage.engine.url.render_as_string(hide_password=False)
    out = tmp_path / "tableau-app"
    done = subprocess.run([sys.executable, str(script), "--database-url", url, "--steam-id", ME, "--out-dir", str(out)],
                          capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert "rounds: 4 rows" in done.stdout and "matches: 1 rows" in done.stdout
    assert (out / "app_rounds.csv").read_bytes() == client.get(ROUNDS_URL).content
