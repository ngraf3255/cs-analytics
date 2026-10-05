"""End-to-end on a REAL CS2 demo: demoparser2 -> feature mapping -> model -> storage -> report.

Skipped unless CSA_TEST_DEMO points at a CS2 .dem / .dem.bz2 (demos are too large to commit).
Known-good public fixture (60 MB, MIT, demoparser2's own test demo)::

    curl -L -o /tmp/test_demo.dem \
      https://raw.githubusercontent.com/LaihoE/demoparser/main/src/parser/test_demo.dem
    CSA_TEST_DEMO=/tmp/test_demo.dem pytest tests/test_real_demo.py
"""

import hashlib
import os

import pytest

from steamlink.demo_parser import Demoparser2Parser
from steamlink.valve import decompress_bz2

from test_api_steam import login, make_client, upload_and_wait

DEMO = os.environ.get("CSA_TEST_DEMO")
pytestmark = pytest.mark.skipif(not DEMO or not os.path.isfile(DEMO), reason="set CSA_TEST_DEMO to a CS2 demo")

# demoparser2 repo src/parser/test_demo.dem: de_mirage, SourceTV, 10 round_end events
# (round 10 is a CT surrender during the round restart: no freeze end, no kills).
DEMOPARSER_FIXTURE_SHA256 = "84a1a4191302bdd2a3bbb5a727842093744b1fb1a228aeec630369e44b622cb2"
# Final scores (sides at the end) checked against the team entities' round totals on the last
# tick (demoparser2 parse_ticks team_rounds_total): demoparser fixture 8-2 (T), awpy public set:
# Valve MM de_ancient 6-2 (surrender), FACEIT de_mirage 13-11 (knife round first), HLTV de_nuke 13-5.
KNOWN_SCORES = {
    DEMOPARSER_FIXTURE_SHA256: {"ct": 2, "t": 8},
    "b29a9cb537a181deef97b15cfed10ee722a37999644a27bb2226fdd77a1337fc": {"ct": 6, "t": 2},  # MM ancient .dem
    "ac1c51a159b80f72b43b88271765457229a3f28f07a25e45d780a9265b3e5a51": {"ct": 13, "t": 11},  # FACEIT mirage .dem
    "679efc6ae7d750e98124149ce5462956d7694b1c0936a655621475c7ed864136": {"ct": 13, "t": 5},  # HLTV nuke .dem
}


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def test_real_demo_upload_parse_score_report(tmp_path):
    client, ctx = make_client(tmp_path)
    login(client, ctx)
    ctx.sync.parser = Demoparser2Parser()  # the real parser, not the fake
    with open(DEMO, "rb") as fh:
        response, job = upload_and_wait(client, ctx, fh.read(), timeout=1200)  # background parse job
    assert response.status_code == 202, response.text
    assert job["status"] == "done" and job["created"] is True, job
    match = job["match"]
    assert match["status"] == "imported" and match["map_name"] and match["rounds_count"] > 0

    report = client.get(f"/matches/{match['id']}").json()
    rounds = report["rounds"]
    assert report["summary"]["scored"] > 0
    for rnd in rounds:
        if rnd["prediction"]:
            assert rnd["actual_winner"] in ("ct", "t")
            assert 0 <= rnd["opening_kill"]["seconds"] <= 120
            assert rnd["prediction"]["probabilities"]["ct"] + rnd["prediction"]["probabilities"]["t"] == pytest.approx(1)
        else:
            assert rnd["unscored_reason"]

    # The final score is read from the demo: every round has a winner, so it adds up.
    assert match["score"] is not None, match
    assert match["score"]["ct"] + match["score"]["t"] <= match["rounds_count"]
    assert max(match["score"].values()) >= 1
    known = KNOWN_SCORES.get(_sha256(DEMO) if not DEMO.endswith(".bz2") else None)
    if known:
        assert match["score"] == known
        # Every round of the match is counted once and warmup / knife rounds are not
        # (FACEIT: knife round, restart, 24 rounds -> 24, not 25).
        assert match["rounds_count"] == known["ct"] + known["t"] == len(rounds)
        assert [r["round_number"] for r in rounds] == list(range(1, len(rounds) + 1))
    if _sha256(DEMO) == DEMOPARSER_FIXTURE_SHA256:
        assert match["map_name"] == "de_mirage" and match["rounds_count"] == 10
        assert [r["actual_winner"] for r in rounds] == ["t", "ct", "t", "t", "ct", "t", "t", "t", "t", "t"]
        assert rounds[0]["opening_kill"] == {"side": "t", "seconds": pytest.approx(20.703125), "weapon": "p250"}
        # Round 7 starts right after a post-round exit frag; it must not count as the opening kill.
        assert rounds[6]["opening_kill"]["seconds"] > 0 and rounds[6]["prediction"] is not None
        assert rounds[9]["unscored_reason"] == "no_opening_kill"
        assert report["summary"]["scored"] == 9

    check_summary_matches_report(client, report)
    with open(DEMO, "rb") as fh:
        _, again = upload_and_wait(client, ctx, fh.read(), timeout=1200)
    assert again["created"] is False and again["match"]["id"] == match["id"]
    check_summary_matches_report(client, report)  # dedupe: still one match


def check_summary_matches_report(client, report):
    """GET /matches/summary over the one real match agrees with its per-round report."""

    summary = client.get("/matches/summary").json()
    rounds = report["rounds"]
    scored = [r for r in rounds if r["prediction"] and r["actual_winner"] in ("ct", "t")]
    assert summary["totals"]["matches"] == summary["totals"]["imported_matches"] == 1
    assert summary["totals"]["rounds"] == len(rounds) == report["match"]["rounds_count"]
    assert summary["prediction"]["scored_rounds"] == len(scored) == report["summary"]["scored"]
    assert summary["prediction"]["correct"] == report["summary"]["correct_predictions"]
    brier = sum((r["prediction"]["probabilities"]["ct"] - (r["actual_winner"] == "ct")) ** 2 for r in scored) / len(scored)
    assert summary["prediction"]["brier_score"] == pytest.approx(brier, abs=1e-4)
    assert sum(b["rounds"] for b in summary["prediction"]["calibration"]) == len(scored)
    winners = [r["actual_winner"] for r in rounds if r["actual_winner"]]
    assert (summary["sides"]["ct_won"], summary["sides"]["t_won"]) == (winners.count("ct"), winners.count("t"))
    (map_view,) = summary["maps"]
    assert map_view["map_name"] == report["match"]["map_name"] and map_view["matches"] == 1
    assert [m["id"] for m in summary["recent_form"]["matches"]] == [report["match"]["id"]]
    unscored = sum(r["rounds"] for r in summary["unscored_reasons"])
    assert unscored == len(rounds) - len(scored) == summary["totals"]["unscored_rounds"]
    if report["match"]["map_name"] == "de_mirage" and len(rounds) == 10 and report["summary"]["scored"] == 9:
        # demoparser2 fixture: T won 8 of 10 rounds; the model's favourite won 8 of 9 scored rounds.
        assert summary["sides"] == {"rounds_with_winner": 10, "ct_won": 2, "t_won": 8, "ct_win_rate": 0.2,
                                    "t_win_rate": 0.8}
        assert summary["prediction"]["hit_rate"] == round(8 / 9, 4)
        assert summary["unscored_reasons"] == [{"reason": "no_opening_kill", "rounds": 1}]


def test_subprocess_and_in_process_parse_identically(tmp_path):
    demo = DEMO
    with open(DEMO, "rb") as fh:
        if fh.read(3) == b"BZh":  # the parser takes a plain .dem; upload decompresses first
            demo = str(tmp_path / "demo.dem")
            decompress_bz2(DEMO, demo, 1 << 32)
    isolated = Demoparser2Parser(isolation="subprocess").parse(demo)
    assert isolated == Demoparser2Parser(isolation="inprocess").parse(demo)
    assert isolated.rounds and isolated.deaths


def test_real_demo_steam_sync_background_job(tmp_path):
    """Steam sync of a real demo (Valve download faked with the local file): the request
    only queues a job; the worker downloads, (decompresses,) parses, scores and stores."""

    from steamlink import sharecode

    from fakes import code
    from test_api_steam import AUTH, H

    client, ctx = make_client(tmp_path)
    login(client, ctx)
    ctx.sync.parser = Demoparser2Parser()
    ctx.sync.max_matches = 1
    share = sharecode.decode(code(1))
    ctx.sync.fetcher.files[f"http://replay1.valve.net/730/{share.match_id}_{share.outcome_id}.dem.bz2"] = DEMO
    assert client.put("/steam/match-access", json={"auth_code": AUTH, "share_code": code(0), "consent": True},
                      headers=H).status_code == 200
    response = client.post("/steam/sync", headers=H)
    assert response.status_code == 202, response.text
    (job,) = response.json()["jobs"]
    assert job["status"] in ("queued", "processing") and job["share_code"] == code(1)
    assert ctx.jobs.wait_idle(1200)
    job = client.get(f"/matches/upload/{job['id']}").json()["job"]
    assert job["status"] == "done" and job["created"] is True, job
    match = job["match"]
    assert match["status"] == "imported" and match["source"] == "steam_sync" and match["rounds_count"] > 0
    assert match["share_code"] == code(1)
    assert client.get(f"/matches/{match['id']}").json()["summary"]["scored"] > 0
    # The same demo uploaded by hand afterwards is recognised (hash of the decompressed .dem).
    with open(DEMO, "rb") as fh:
        _, again = upload_and_wait(client, ctx, fh.read(), timeout=1200)
    assert again["created"] is False and again["match"]["id"] == match["id"]


# A player of the demoparser2 fixture (on T all 10 rounds, T won 8-2): 11 kills, 4 deaths
# (the in-game scoreboard's kills_total / deaths_total at the end), 1 opening kill, survived 6 rounds.
FIXTURE_PLAYER = "76561198265366770"


def _plain_demo(tmp_path):
    with open(DEMO, "rb") as fh:
        if fh.read(3) != b"BZh":
            return DEMO
    demo = str(tmp_path / "demo.dem")
    decompress_bz2(DEMO, demo, 1 << 32)
    return demo


def test_player_sides_and_kd_match_the_demos_own_player_state(tmp_path):
    """Independent check of extract_player_rounds: every player's side each round equals
    their team_num at that round's freeze end (parse_ticks, a different code path in the
    parser), and their kills / deaths add up to the scoreboard totals at the end."""

    from collections import Counter

    from demoparser2 import DemoParser

    from steamlink.demo_parser import TEAM_NUM_TO_SIDE, extract_player_rounds, match_rounds

    demo_path = _plain_demo(tmp_path)
    demo = Demoparser2Parser(isolation="inprocess").parse(demo_path)
    records = extract_player_rounds(demo)
    assert records, "no player rounds"
    rounds = match_rounds(demo)  # numbered from the match start, like the records
    with_freeze = [r for r in rounds if r.freeze_end_tick is not None]
    final_tick = rounds[-1].end_tick
    frame = DemoParser(demo_path).parse_ticks(["team_num", "kills_total", "deaths_total", "is_alive"],
                                              ticks=[r.freeze_end_tick for r in with_freeze] + [final_tick])
    by_key = {(r.round_number, r.steam_id): r for r in records}
    checked = missing = 0
    for rnd in with_freeze:
        for row in frame[frame["tick"] == rnd.freeze_end_tick].itertuples():
            side = TEAM_NUM_TO_SIDE.get(int(row.team_num)) if row.team_num == row.team_num else None
            record = by_key.get((rnd.number, str(row.steamid)))
            if side is not None and record is None and row.is_alive:
                missing += 1  # alive on a team at freeze end but not tracked
            if side is None or record is None:
                continue  # spectator / not tracked this round (e.g. disconnected)
            assert record.side == side, (rnd.number, row.steamid)
            checked += 1
    assert missing == 0  # e.g. HLTV de_nuke round 1: recorded although the demo has no spawn in it
    checkable = {r.number for r in with_freeze}
    assert checked >= 0.9 * sum(1 for r in records if r.round_number in checkable)
    kills, deaths = Counter(), Counter()
    for r in records:
        kills[r.steam_id] += r.kills
        deaths[r.steam_id] += r.deaths
    for row in frame[frame["tick"] == final_tick].itertuples():
        steam_id = str(row.steamid)
        if steam_id in kills:
            assert (kills[steam_id], deaths[steam_id]) == (row.kills_total, row.deaths_total), steam_id
    # Exactly one opening kill and one opening death per round that had one.
    per_round = Counter(r.round_number for r in records if r.opening_kill)
    assert all(n == 1 for n in per_round.values())
    if _sha256(demo_path) == DEMOPARSER_FIXTURE_SHA256:
        mine = [r for r in records if r.steam_id == FIXTURE_PLAYER]
        assert [r.side for r in mine] == ["t"] * 10
        assert (sum(r.kills for r in mine), sum(r.deaths for r in mine), sum(r.opening_kill for r in mine),
                sum(r.opening_death for r in mine), sum(r.survived for r in mine)) == (11, 4, 1, 0, 6)


def test_real_demo_personal_analytics_for_a_player_in_the_demo(tmp_path):
    """Sign in as a SteamID that is really in the demo, upload it, and check the ``you``
    numbers of GET /matches/summary and GET /matches/{id} against the parse."""

    from steamlink.demo_parser import extract_player_rounds, extract_rounds

    from test_summary import session

    demo_path = _plain_demo(tmp_path)
    parsed = Demoparser2Parser(isolation="inprocess").parse(demo_path)
    records = extract_player_rounds(parsed)
    is_fixture = _sha256(demo_path) == DEMOPARSER_FIXTURE_SHA256
    player = FIXTURE_PLAYER if is_fixture else max({r.steam_id for r in records},
                                                    key=lambda s: sum(r.steam_id == s for r in records))
    mine = [r for r in records if r.steam_id == player]
    winners = {r.round_number: r.winner_side for r in extract_rounds(parsed)}

    client, ctx = make_client(tmp_path)
    ctx.sync.parser = Demoparser2Parser()
    me = session(client, ctx, player)
    with open(DEMO, "rb") as fh:
        response, job = upload_and_wait(me, ctx, fh.read(), timeout=1200)
    assert response.status_code == 202 and job["status"] == "done", job
    match_id = job["match"]["id"]
    assert job["match"]["players_recorded"] is True and job["match"]["date_source"] == "imported"

    you = me.get("/matches/summary").json()["you"]
    won = sum(1 for r in mine if winners[r.round_number] == r.side)
    assert (you["steam_id"], you["matches"], you["rounds"], you["won"]) == (player, 1, len(mine), won)
    assert (you["kills"], you["deaths"]) == (sum(r.kills for r in mine), sum(r.deaths for r in mine))
    assert you["sides"]["ct"]["rounds"] + you["sides"]["t"]["rounds"] == you["rounds_with_winner"]
    report = me.get(f"/matches/{match_id}").json()
    assert report["you"]["status"] == "in_match" and report["you"]["rounds"] == len(mine)
    assert [r["you"]["side"] for r in report["rounds"] if r["you"]] == [r.side for r in mine]
    score = report["match"]["score"]
    if score:
        side = mine[-1].side
        assert report["you"]["score"] == {"you": score[side], "them": score["t" if side == "ct" else "ct"]}
    if is_fixture:  # on T all match, T won 8-2
        assert (you["won"], you["win_rate"], you["sides"]["t"]["win_rate"], you["kd"]) == (8, 0.8, 0.8, 2.75)
        assert you["opening_duels"]["taken"] == 1 and you["opening_duels"]["win_rate"] == 1.0
        assert report["you"]["result"] == "won" and report["you"]["score"] == {"you": 8, "them": 2}

    # Someone who is not in the demo, sharing the same match, gets "not in this match".
    other = session(client, ctx, "76561198000000555")
    with open(DEMO, "rb") as fh:
        upload_and_wait(other, ctx, fh.read(), timeout=1200)
    assert other.get(f"/matches/{match_id}").json()["you"]["status"] == "not_in_match"
    other_you = other.get("/matches/summary").json()["you"]
    assert (other_you["matches"], other_you["matches_without_you"]) == (0, 1)
