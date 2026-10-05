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

    if _sha256(DEMO) == DEMOPARSER_FIXTURE_SHA256:
        assert match["map_name"] == "de_mirage" and match["rounds_count"] == 10
        assert [r["actual_winner"] for r in rounds] == ["t", "ct", "t", "t", "ct", "t", "t", "t", "t", "t"]
        assert rounds[0]["opening_kill"] == {"side": "t", "seconds": pytest.approx(20.703125), "weapon": "p250"}
        # Round 7 starts right after a post-round exit frag; it must not count as the opening kill.
        assert rounds[6]["opening_kill"]["seconds"] > 0 and rounds[6]["prediction"] is not None
        assert rounds[9]["unscored_reason"] == "no_opening_kill"
        assert report["summary"]["scored"] == 9

    with open(DEMO, "rb") as fh:
        _, again = upload_and_wait(client, ctx, fh.read(), timeout=1200)
    assert again["created"] is False and again["match"]["id"] == match["id"]


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
