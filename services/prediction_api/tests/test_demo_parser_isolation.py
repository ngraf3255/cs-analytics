"""Demo parsing in a child process, the shared parse slot, and parser settings (no real demo needed)."""

import subprocess
import threading

import pytest

from steamlink import demo_parser, upload
from steamlink.config import ConfigError, load_settings
from steamlink.demo_parser import (
    PARSE_SLOT, DemoParseError, Demoparser2Parser, ParsedDeath, ParsedDemo, ParsedRound,
    parsed_demo_from_json, parsed_demo_to_json,
)

from test_sync import env  # noqa: F401  (fixture)

SAMPLE = ParsedDemo(
    map_name="de_mirage",
    rounds=[ParsedRound(1, 1000, 8000, "t"), ParsedRound(2, None, 15000, None)],
    deaths=[ParsedDeath(2280, "t", "ct", "weapon_ak47"), ParsedDeath(9640, None, "t", "world")],
)


def test_json_round_trip_is_lossless():
    assert parsed_demo_from_json(parsed_demo_to_json(SAMPLE)) == SAMPLE


@pytest.mark.parametrize("isolation", ["subprocess", "inprocess"])
def test_garbage_file_is_a_parse_error_in_both_modes(tmp_path, isolation):
    bad = tmp_path / "bad.dem"
    bad.write_bytes(b"PBDEMS2\0" + b"\x00garbage" * 64)
    with pytest.raises(DemoParseError):
        Demoparser2Parser(isolation=isolation).parse(str(bad))


def test_missing_file_is_a_parse_error(tmp_path):
    with pytest.raises(DemoParseError):
        Demoparser2Parser().parse(str(tmp_path / "missing.dem"))


def _fake_run(result=None, exc=None, seen=None):
    def run(cmd, **kwargs):
        if seen is not None:
            seen.update(cmd=cmd, **kwargs)
        if exc is not None:
            raise exc
        return result
    return run


def test_worker_contract_env_and_timeout(monkeypatch):
    seen = {}
    ok = subprocess.CompletedProcess([], 0, stdout=demo_parser.json.dumps(parsed_demo_to_json(SAMPLE)).encode(),
                                     stderr=b"")
    monkeypatch.setattr(demo_parser.subprocess, "run", _fake_run(ok, seen=seen))
    monkeypatch.delenv("RAYON_NUM_THREADS", raising=False)
    assert Demoparser2Parser(timeout_seconds=12, threads=3).parse("/x/demo.dem") == SAMPLE
    assert seen["cmd"][1:] == ["-m", "steamlink.parse_worker", "/x/demo.dem"]
    assert seen["timeout"] == 12 and seen["env"]["RAYON_NUM_THREADS"] == "3"
    assert seen["env"]["MALLOC_ARENA_MAX"] == "2"


@pytest.mark.parametrize("outcome", [
    {"exc": subprocess.TimeoutExpired("worker", 1)},
    {"exc": OSError("no python")},
    {"result": subprocess.CompletedProcess([], -9, stdout=b"", stderr=b"")},  # OOM-killed child
    {"result": subprocess.CompletedProcess([], 2, stdout=b"", stderr=b"demo could not be parsed")},
    {"result": subprocess.CompletedProcess([], 0, stdout=b"not json", stderr=b"")},
    {"result": subprocess.CompletedProcess([], 0, stdout=b'{"map_name": null}', stderr=b"")},
])
def test_worker_failures_become_parse_errors(monkeypatch, outcome):
    monkeypatch.setattr(demo_parser.subprocess, "run", _fake_run(**outcome))
    with pytest.raises(DemoParseError):
        Demoparser2Parser().parse("/x/demo.dem")


def test_unknown_isolation_rejected():
    with pytest.raises(ValueError):
        Demoparser2Parser(isolation="thread")


def test_upload_and_sync_share_one_parse_slot():
    assert upload._parse_slot is PARSE_SLOT


def test_sync_waits_for_a_running_parse(env):  # noqa: F811
    svc = env["service"](max_matches=1)
    assert PARSE_SLOT.acquire(blocking=False)  # e.g. an upload is parsing
    released = False
    try:
        worker = threading.Thread(target=lambda: svc.sync(env["user"]))
        worker.start()
        worker.join(0.3)
        assert worker.is_alive() and env["parser"].calls == 0
    finally:
        PARSE_SLOT.release()
        released = True
    worker.join(5)
    assert released and not worker.is_alive() and env["parser"].calls == 1


def test_parse_settings_defaults_and_validation():
    settings = load_settings({})
    assert (settings.demo_parse_isolation, settings.demo_parse_timeout_seconds, settings.demo_parse_threads) == (
        "subprocess", 600.0, 2)
    assert load_settings({"DEMO_PARSE_ISOLATION": "InProcess"}).demo_parse_isolation == "inprocess"
    base = {"DATABASE_URL": "sqlite://", "TOKEN_ENCRYPTION_KEYS": "k", "SESSION_SECRET": "s" * 40,
            "PUBLIC_API_URL": "https://api.example.com", "FRONTEND_URL": "https://example.com",
            "STEAM_WEB_API_KEY": "k"}
    load_settings(base)
    for bad in ({"DEMO_PARSE_ISOLATION": "thread"}, {"DEMO_PARSE_THREADS": "0"}, {"DEMO_PARSE_TIMEOUT_SECONDS": "0"}):
        with pytest.raises(ConfigError):
            load_settings({**base, **bad})
