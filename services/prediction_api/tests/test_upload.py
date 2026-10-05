"""Manual .dem upload endpoint: auth, CSRF/origin, flag, limits, sniffing, idempotency."""

import bz2
import os
import tempfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

import main
from steamlink import upload
from steamlink.demo_parser import DemoParseError, DemoParser, ParsedDemo

from fakes import FakeParser
from test_api_steam import H, login, make_client

OCTET = {**H, "Content-Type": "application/octet-stream"}
DEMO = b"PBDEMS2\0" + b"\x01" * 4096


class CountingParser(FakeParser):
    pass


class BrokenParser(DemoParser):
    def parse(self, demo_path):
        raise DemoParseError("boom")


class EmptyParser(DemoParser):
    def parse(self, demo_path):
        return ParsedDemo(map_name="de_mirage")


@pytest.fixture()
def up(tmp_path, monkeypatch):
    # Route the endpoint's temp dirs into tmp_path so cleanup can be asserted.
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))
    client, ctx = make_client(tmp_path)
    login(client, ctx)
    ctx.sync.parser = CountingParser()
    return client, ctx, scratch


def post(client, body, headers=OCTET):
    return client.post("/matches/upload", content=body, headers=headers)


def test_disabled_flag_returns_503():
    response = TestClient(main.app).post("/matches/upload", content=DEMO, headers=OCTET)
    assert response.status_code == 503 and response.json()["detail"] == "steam_sync_disabled"


def test_requires_session(tmp_path):
    client, _ = make_client(tmp_path)
    assert post(client, DEMO).status_code == 401


def test_requires_csrf_header_and_allowed_origin(up):
    client, ctx, _ = up
    assert post(client, DEMO, {"Content-Type": "application/octet-stream"}).status_code == 403
    evil = {**OCTET, "Origin": "https://evil.example"}
    response = post(client, DEMO, evil)
    assert response.status_code == 403 and response.json()["detail"] == "origin_not_allowed"
    assert ctx.sync.parser.calls == 0


def test_upload_lists_and_reports_and_dedupes_without_reparsing(up):
    client, ctx, scratch = up
    first = post(client, DEMO)
    assert first.status_code == 200, first.text
    match = first.json()["match"]
    assert first.json()["created"] is True
    assert match["source"] == "upload" and match["share_code"] is None and match["rounds_count"] == 2
    # .dem.bz2 of the same demo is the same match, and is not parsed again.
    again = post(client, bz2.compress(DEMO)).json()
    assert again == {"match": match, "created": False}
    assert ctx.sync.parser.calls == 1
    listed = client.get("/matches").json()["matches"]
    assert [m["id"] for m in listed] == [match["id"]] and listed[0]["source"] == "upload"
    report = client.get(f"/matches/{match['id']}").json()
    assert report["summary"]["rounds"] == 2 and report["summary"]["scored"] == 1
    assert report["rounds"][0]["prediction"]["probabilities"]["t"] > 0
    # Uploads never create a share-code link or move a cursor.
    assert client.get("/me").json()["match_access"]["linked"] is False
    assert os.listdir(scratch) == []  # temp files removed


@pytest.mark.parametrize("payload", [
    b"",
    b"HL2DEMO\0" + b"\0" * 100,  # CS:GO demo
    b"PK\x03\x04 not a demo",
    b"BZh91AY&SY" + b"\xff" * 64,  # corrupt bzip2
    bz2.compress(b"hello, not a demo"),
])
def test_rejects_files_that_are_not_cs2_demos(up, payload):
    client, ctx, scratch = up
    response = post(client, payload)
    assert response.status_code == 422 and response.json()["detail"] == "not_a_cs2_demo"
    assert ctx.sync.parser.calls == 0
    assert client.get("/matches").json()["matches"] == []
    assert os.listdir(scratch) == []


def test_size_limits(up):
    client, ctx, scratch = up
    ctx.settings = replace(ctx.settings, demo_max_decompressed_bytes=1024)
    assert post(client, DEMO).status_code == 413  # raw stream over the limit
    compressed = bz2.compress(DEMO)
    assert len(compressed) < 1024
    response = post(client, compressed)  # small archive that inflates past the limit
    assert response.status_code == 413 and response.json()["detail"] == "demo_too_large"
    ctx.settings = replace(ctx.settings, demo_max_decompressed_bytes=1 << 20, demo_max_download_bytes=10)
    assert post(client, compressed).status_code == 413  # compressed size cap
    assert ctx.sync.parser.calls == 0
    assert os.listdir(scratch) == []


@pytest.mark.parametrize("parser,detail", [(BrokenParser(), "demo_parse_failed"), (EmptyParser(), "demo_has_no_rounds")])
def test_parse_problems_store_nothing(up, parser, detail):
    client, ctx, scratch = up
    ctx.sync.parser = parser
    response = post(client, DEMO)
    assert response.status_code == 422 and response.json()["detail"] == detail
    assert client.get("/matches").json()["matches"] == []
    assert os.listdir(scratch) == []


def test_busy_when_another_parse_is_running(up):
    client, ctx, _ = up
    assert upload._parse_slot.acquire(blocking=False)
    try:
        response = post(client, DEMO)
    finally:
        upload._parse_slot.release()
    assert response.status_code == 429 and response.json()["detail"] == "upload_busy"
    assert post(client, DEMO).status_code == 200


def test_share_code_query_param_links_the_upload(up):
    from fakes import code

    client, ctx, scratch = up
    bad = client.post("/matches/upload?share_code=CSGO-nope", content=DEMO, headers=OCTET)
    assert bad.status_code == 422 and bad.json()["detail"] == "invalid_share_code_format"
    response = client.post(f"/matches/upload?share_code={code(7)}", content=DEMO, headers=OCTET)
    assert response.status_code == 200, response.text
    assert response.json()["match"]["share_code"] == code(7) and response.json()["match"]["source"] == "upload"
    assert ctx.sync.parser.calls == 1


def test_upload_max_bytes_caps_the_request_body(up):
    client, ctx, scratch = up
    ctx.settings = replace(ctx.settings, upload_max_bytes=1024)
    response = post(client, DEMO)  # 4 KiB body, decompressed limit still 1 GiB
    assert response.status_code == 413 and response.json()["detail"] == "demo_too_large"
    ctx.settings = replace(ctx.settings, upload_max_bytes=1 << 20)
    assert post(client, DEMO).status_code == 200
    assert os.listdir(scratch) == []


def test_upload_max_bytes_from_env():
    from steamlink.config import ConfigError, load_settings

    assert load_settings({"UPLOAD_MAX_BYTES": "104857600"}).upload_max_bytes == 100 * 1024 * 1024
    assert load_settings({}).upload_max_bytes == 1024 * 1024 * 1024
    with pytest.raises(ConfigError):
        load_settings({"UPLOAD_MAX_BYTES": "abc"})
