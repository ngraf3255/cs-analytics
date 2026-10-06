"""Demo parsing in a child process, the shared parse slot, and parser settings (no real demo needed)."""

import subprocess
import threading

import pytest

from steamlink import demo_parser, upload
from steamlink.config import ConfigError, load_settings
from steamlink.demo_parser import (
    PARSE_SLOT, DemoParseError, Demoparser2Parser, ParsedDeath, ParsedDemo, ParsedRound, ParsedSpawn,
    parsed_demo_from_json, parsed_demo_to_json,
)

from test_sync import env  # noqa: F401  (fixture)

SAMPLE = ParsedDemo(
    map_name="de_mirage",
    rounds=[ParsedRound(1, 1000, 8000, "t"), ParsedRound(2, None, 15000, None)],
    deaths=[ParsedDeath(2280, "t", "ct", "weapon_ak47", 0, 0, "76561198000000001", "76561198000000002"),
            ParsedDeath(9640, None, "t", "world", None, 1, None, "76561198000000001")],
    spawns=[ParsedSpawn(500, "76561198000000001", "t"), ParsedSpawn(500, "76561198000000002", "ct")],
    match_start_tick=60,
)


def test_json_round_trip_is_lossless():
    assert parsed_demo_from_json(parsed_demo_to_json(SAMPLE)) == SAMPLE


def test_json_from_an_older_worker_without_players_still_loads():
    old = parsed_demo_to_json(SAMPLE)
    old["deaths"] = [d[:6] for d in old["deaths"]]
    del old["spawns"], old["match_start_tick"]
    demo = parsed_demo_from_json(old)
    assert demo.spawns == [] and demo.match_start_tick is None
    assert demo.deaths[0].attacker_steamid is None and demo.deaths[0].weapon == "weapon_ak47"


@pytest.mark.parametrize("isolation", ["subprocess", "inprocess"])
def test_garbage_file_is_a_parse_error_in_both_modes(tmp_path, isolation):
    bad = tmp_path / "bad.dem"
    body = b"\x00garbage" * 64
    bad.write_bytes(b"PBDEMS2\0" + demo_parser.struct.pack("<ii", 8 + len(body) - 20, 0) + body)
    with pytest.raises(DemoParseError) as err:
        Demoparser2Parser(isolation=isolation).parse(str(bad))
    assert err.value.reason in demo_parser.DEMO_PARSE_REASONS


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


@pytest.fixture
def demo_file(tmp_path):
    """A file with a complete CS2 header (the parser checks it before starting the worker)."""

    path = tmp_path / "demo.dem"
    path.write_bytes(b"PBDEMS2\0" + demo_parser.struct.pack("<ii", 16 + 64 - 20, 0) + b"\x00" * 64)
    return str(path)


def test_worker_contract_env_and_timeout(monkeypatch, demo_file):
    seen = {}
    ok = subprocess.CompletedProcess([], 0, stdout=demo_parser.json.dumps(parsed_demo_to_json(SAMPLE)).encode(),
                                     stderr=b"")
    monkeypatch.setattr(demo_parser.subprocess, "run", _fake_run(ok, seen=seen))
    monkeypatch.delenv("RAYON_NUM_THREADS", raising=False)
    assert Demoparser2Parser(timeout_seconds=12, threads=3).parse(demo_file) == SAMPLE
    assert seen["cmd"][1:] == ["-m", "steamlink.parse_worker", demo_file]
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
def test_worker_failures_become_parse_errors(monkeypatch, outcome, demo_file):
    monkeypatch.setattr(demo_parser.subprocess, "run", _fake_run(**outcome))
    with pytest.raises(DemoParseError):
        Demoparser2Parser().parse(demo_file)


def test_unknown_isolation_rejected():
    with pytest.raises(ValueError):
        Demoparser2Parser(isolation="thread")


def test_upload_and_sync_share_one_parse_slot():
    assert upload._parse_slot is PARSE_SLOT


def test_sync_job_waits_for_a_running_parse(env):  # noqa: F811
    env.sync(env.service(max_matches=1))  # the request only queues the job: no parse slot needed
    assert PARSE_SLOT.acquire(blocking=False)  # e.g. an upload is parsing
    released = False
    try:
        worker = threading.Thread(target=env.drain)
        worker.start()
        worker.join(0.3)
        assert worker.is_alive() and env.parser.calls == 0
    finally:
        PARSE_SLOT.release()
        released = True
    worker.join(5)
    assert released and not worker.is_alive() and env.parser.calls == 1


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


# Failure reasons, diagnostics and the pre-parse header check --------------------------------

def _cs2_header(fileinfo_offset: int, body: bytes = b"\x00" * 64) -> bytes:
    return b"PBDEMS2\0" + demo_parser.struct.pack("<ii", fileinfo_offset, 0) + body


@pytest.mark.parametrize(("text", "reason"), [
    ("Exception('MalformedMessage')", "demo_format_unsupported"),
    ("demo could not be parsed: Exception('EntityNotFound')", "demo_format_unsupported"),
    ("Exception('UnknownDemoCmd(34)')", "demo_format_unsupported"),
    ("Exception('ClassMapperNotFoundFirstPass')", "demo_format_unsupported"),
    ("Exception('DemoEndsEarly(\"x\")')", "demo_truncated"),
    ("Exception('Source1DemoError')", "not_a_cs2_demo"),
    ("Exception('UnknownFile')", "not_a_cs2_demo"),
    ("Exception('VectorResizeFailure')", "demo_parse_failed"),
    ("", "demo_parse_failed"),
])
def test_parser_errors_are_classified(text, reason):
    assert demo_parser.classify_parser_error(text) == reason


def test_header_check_accepts_a_complete_demo_and_an_unfinalized_one(tmp_path):
    body = b"\x01" * 200
    complete = tmp_path / "ok.dem"
    complete.write_bytes(_cs2_header(16 + len(body) - 20, body))
    info = demo_parser.inspect_demo_file(str(complete))
    assert info.problem is None and info.magic == b"PBDEMS2\0" and info.size == 216
    unfinalized = tmp_path / "unfinalized.dem"
    unfinalized.write_bytes(_cs2_header(0, body))  # recording never finalized: offset 0, still parsed
    assert demo_parser.inspect_demo_file(str(unfinalized)).problem is None


@pytest.mark.parametrize(("content", "problem"), [
    (_cs2_header(10_000_000), "demo_truncated"),  # header points past the end: a cut-off copy / download
    (b"PBDEMS2\0\x01\x02", "demo_truncated"),  # shorter than the header
    (b"PBDE", "demo_truncated"),
    (b"HL2DEMO\0" + b"\x00" * 64, "not_a_cs2_demo"),  # CS:GO
    (b"<html>error</html>", "not_a_cs2_demo"),
    (b"", "not_a_cs2_demo"),
])
def test_header_check_rejects_before_parsing(tmp_path, monkeypatch, content, problem):
    path = tmp_path / "x.dem"
    path.write_bytes(content)
    assert demo_parser.inspect_demo_file(str(path)).problem == problem

    def no_parse(*args, **kwargs):
        raise AssertionError("must not start the parser")

    monkeypatch.setattr(demo_parser.subprocess, "run", no_parse)
    with pytest.raises(DemoParseError) as err:
        Demoparser2Parser().parse(str(path))
    assert err.value.reason == problem


def test_worker_reason_line_and_diagnostics_are_logged(tmp_path, monkeypatch, caplog):
    demo = tmp_path / "demo.dem"
    demo.write_bytes(_cs2_header(16 + 64 - 20))
    stderr = (b"Traceback (most recent call last):\n  ...\nException: MalformedMessage\n"
              b"demo: size=80 demoparser2=0.42.0\nDEMO_PARSE_REASON=demo_format_unsupported\n")
    monkeypatch.setattr(demo_parser.subprocess, "run",
                        _fake_run(subprocess.CompletedProcess([], 2, stdout=b"", stderr=stderr)))
    with caplog.at_level("WARNING"), pytest.raises(DemoParseError) as err:
        Demoparser2Parser().parse(str(demo))
    assert err.value.reason == "demo_format_unsupported"
    logged = caplog.text
    assert "exited with 2" in logged and "Exception: MalformedMessage" in logged
    assert "size=80" in logged and "PBDEMS2" in logged and "fileinfo_offset=60" in logged


@pytest.mark.parametrize(("stderr", "returncode", "reason"), [
    (b"DEMO_PARSE_REASON=demo_truncated\n", 2, "demo_truncated"),
    (b"DEMO_PARSE_REASON=something_new\n", 2, "demo_parse_failed"),  # unknown codes are never passed on
    (b"", -9, "demo_parse_failed"),  # OOM-killed: no reason line
])
def test_worker_reason_is_passed_on(tmp_path, monkeypatch, stderr, returncode, reason):
    demo = tmp_path / "demo.dem"
    demo.write_bytes(_cs2_header(16 + 64 - 20))
    monkeypatch.setattr(demo_parser.subprocess, "run",
                        _fake_run(subprocess.CompletedProcess([], returncode, stdout=b"", stderr=stderr)))
    with pytest.raises(DemoParseError) as err:
        Demoparser2Parser().parse(str(demo))
    assert err.value.reason == reason


def test_timeout_has_its_own_reason(tmp_path, monkeypatch):
    demo = tmp_path / "demo.dem"
    demo.write_bytes(_cs2_header(16 + 64 - 20))
    monkeypatch.setattr(demo_parser.subprocess, "run", _fake_run(exc=subprocess.TimeoutExpired("worker", 1)))
    with pytest.raises(DemoParseError) as err:
        Demoparser2Parser().parse(str(demo))
    assert err.value.reason == "demo_parse_timeout"


def test_real_worker_prints_traceback_file_info_and_reason(tmp_path):
    bad = tmp_path / "bad.dem"
    bad.write_bytes(_cs2_header(16 + 512 - 20, b"\x00garbage" * 64))
    import os
    import sys

    package_root = os.path.dirname(os.path.dirname(os.path.abspath(demo_parser.__file__)))
    done = subprocess.run([sys.executable, "-m", "steamlink.parse_worker", str(bad)], capture_output=True,
                          cwd=package_root, timeout=120, check=False)
    stderr = done.stderr.decode()
    assert done.returncode == 2
    assert "Traceback (most recent call last)" in stderr
    assert "size=528" in stderr and "PBDEMS2" in stderr and "demoparser2=" in stderr
    assert stderr.strip().splitlines()[-1].startswith("DEMO_PARSE_REASON=")
