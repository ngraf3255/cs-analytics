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

from test_api_steam import H, login, make_client

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
        response = client.post("/matches/upload", content=fh.read(),
                               headers={**H, "Content-Type": "application/octet-stream"})
    assert response.status_code == 200, response.text
    match = response.json()["match"]
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

    again = client.post("/matches/upload", content=open(DEMO, "rb").read(),
                        headers={**H, "Content-Type": "application/octet-stream"}).json()
    assert again["created"] is False and again["match"]["id"] == match["id"]


def test_subprocess_and_in_process_parse_identically():
    isolated = Demoparser2Parser(isolation="subprocess").parse(DEMO)
    assert isolated == Demoparser2Parser(isolation="inprocess").parse(DEMO)
    assert isolated.rounds and isolated.deaths
