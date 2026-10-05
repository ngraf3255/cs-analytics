"""``python -m steamlink.live_check``: the one-command live E2E check.

Fake mode (fake Valve HTTP + fake Game Coordinator) runs the real clients,
download, import pipeline, sync job worker, storage and report; the "live"
tests inject the same doubles into the live code path. Runs on SQLite, and on
PostgreSQL with CSA_TEST_DATABASE_URL (the --database-url tests)."""

import bz2
import io
import json
import os

import re

import pytest
from cryptography.fernet import Fernet

from steamlink import live_check
from steamlink.gc import GCAuthError, GCMatch, GCNotReady, GCTimeout, GameCoordinator
from steamlink.live_check import FAKE_API_KEY, FAKE_AUTH_CODE, FAKE_STEAM_ID, FakeValve, fake_codes, run_check

from dbutil import make_test_engine
from fakes import FakeParser

DEMO = b"PBDEMS2\0" + b"\x07" * 50_000
FERNET = Fernet.generate_key().decode()
REFRESH_TOKEN = "eyJ-secret-refresh-token-value"
STATUS_RE = re.compile(r"^\[(\d)/7\] .{22} (PASS|FAIL|SKIP) ", re.M)


@pytest.fixture
def demo_file(tmp_path):
    path = tmp_path / "match.dem"
    path.write_bytes(DEMO)
    return str(path)


def run(argv, *, env=None, **kw):
    out = io.StringIO()
    code = run_check(argv, env={} if env is None else env, out=out, **kw)
    return code, out.getvalue()


def statuses(text):
    """{step number: PASS/FAIL/SKIP} from the result lines."""

    return {int(m.group(1)): m.group(2) for m in STATUS_RE.finditer(text)}


def live_env(**extra):
    env = {"STEAM_WEB_API_KEY": FAKE_API_KEY, "STEAM_BOT_REFRESH_TOKEN": REFRESH_TOKEN,
           "TOKEN_ENCRYPTION_KEYS": FERNET, "CSA_LIVE_STEAM_ID": FAKE_STEAM_ID,
           "CSA_LIVE_AUTH_CODE": FAKE_AUTH_CODE, "CSA_LIVE_KNOWN_CODE": fake_codes(3)[0]}
    env.update(extra)
    return {k: v for k, v in env.items() if v is not None}


class GC(GameCoordinator):
    def __init__(self, codes=None, error=None, result="ok"):
        self.codes, self.error, self.result = codes or fake_codes(3), error, result
        self.closed = False

    def full_match_info(self, match_id, outcome_id, token):
        if self.error:
            raise self.error
        if self.result == "none":
            return None
        if self.result == "no_url":
            return GCMatch(match_id, None, ("", ""))
        if self.result == "bad_url":
            return GCMatch(match_id, None, ("http://evil.example.com/730/1_2.dem.bz2",))
        return live_check.fake_game_coordinator(self.codes).full_match_info(match_id, outcome_id, token)

    def close(self):
        self.closed = True


def live_doubles(demo, *, codes=None, gc=None, valve=None):
    codes = codes or fake_codes(3)
    valve = valve or FakeValve(codes, demo=demo)
    gc = gc or GC(codes)
    return dict(http=valve.client(), make_gc=lambda: gc, parser=FakeParser()), valve, gc


# --- fake mode ------------------------------------------------------------------------

def test_fake_mode_end_to_end(tmp_path, demo_file):
    report_path = tmp_path / "report.json"
    code, out = run(["--fake", "--demo", demo_file, "--report-json", str(report_path)], parser=FakeParser())
    assert code == 0, out
    assert statuses(out) == {i: "PASS" for i in range(1, 8)}
    assert "3 newer share code(s) walked" in out
    assert "sync: status=partial queued=1" in out
    assert "Round report (de_mirage" in out
    assert "RESULT: PASS (7/7 steps, FAKE" in out
    report = json.loads(report_path.read_text())
    assert report["match"]["status"] == "imported" and report["match"]["source"] == "steam_sync"
    assert report["summary"]["rounds"] == 2 and len(report["rounds"]) == 2
    assert report["rounds"][0]["prediction"] is not None  # model-scored (de_mirage, ak47)


def test_fake_mode_bz2_download_is_decompressed(tmp_path):
    path = tmp_path / "match.dem.bz2"
    path.write_bytes(bz2.compress(DEMO))
    code, out = run(["--fake", "--demo", str(path), "--walk", "1"], parser=FakeParser())
    assert code == 0, out
    assert "bz2 -> 0.1 MB .dem" in out


def test_fake_mode_needs_a_demo():
    code, out = run(["--fake"])
    assert code == 2
    assert "--fake needs a local CS2 demo: pass --demo PATH" in out
    assert statuses(out) == {1: "FAIL", **{i: "SKIP" for i in range(2, 8)}}


def test_fake_mode_demo_from_env(demo_file):
    code, out = run(["--fake"], env={"CSA_LIVE_FAKE_DEMO": demo_file}, parser=FakeParser())
    assert code == 0, out


def test_fake_mode_ignores_real_secrets_in_env(demo_file):
    env = {"STEAM_WEB_API_KEY": "real-key-must-not-be-used", "DATABASE_URL": "postgresql://prod/db"}
    valve = FakeValve(fake_codes(3), demo=demo_file)
    code, out = run(["--fake", "--demo", demo_file], env=env, parser=FakeParser(), http=valve.client())
    assert code == 0, out
    assert "real-key-must-not-be-used" not in out and "prod" not in out


def test_fake_mode_wrong_auth_code_fails_at_history(demo_file):
    code, out = run(["--fake", "--demo", demo_file, "--auth-code", "ZZZZ-ZZZZZ-ZZZZ"], parser=FakeParser())
    assert code == 1
    assert "Valve rejected the game authentication code (HTTP 403)" in out
    assert statuses(out)[3] == "FAIL" and statuses(out)[4] == "SKIP"


REAL_DEMO = os.environ.get("CSA_TEST_DEMO")


@pytest.mark.skipif(not REAL_DEMO or not os.path.isfile(REAL_DEMO), reason="set CSA_TEST_DEMO to a CS2 demo")
def test_fake_mode_real_demo_real_parser_per_round_analytics(tmp_path):
    report_path = tmp_path / "report.json"
    code, out = run(["--fake", "--demo", REAL_DEMO, "--walk", "2", "--report-json", str(report_path)])
    assert code == 0, out
    report = json.loads(report_path.read_text())
    assert report["summary"]["rounds"] >= 8
    assert report["summary"]["scored"] >= 1
    assert any(r["prediction"] for r in report["rounds"])
    assert report["match"]["score"] is not None


# --- missing prerequisites ------------------------------------------------------------

def test_live_with_nothing_configured_lists_every_missing_prerequisite():
    code, out = run([])
    assert code == 2
    for needle in ("STEAM_WEB_API_KEY is not set", "demo bot credentials are not set",
                   "python -m steamlink.bot_login", "TOKEN_ENCRYPTION_KEYS is not set", "Steam ID is not set",
                   "game authentication code is not set", "known share code is not set"):
        assert needle in out
    assert statuses(out) == {1: "FAIL", **{i: "SKIP" for i in range(2, 8)}}
    assert "RESULT: BLOCKED at step 1 (config)" in out
    assert "docs/live-e2e-checklist.md" in out


@pytest.mark.parametrize("missing, needle", [
    ("STEAM_WEB_API_KEY", "STEAM_WEB_API_KEY is not set"),
    ("STEAM_BOT_REFRESH_TOKEN", "demo bot credentials are not set"),
    ("TOKEN_ENCRYPTION_KEYS", "TOKEN_ENCRYPTION_KEYS is not set"),
    ("CSA_LIVE_STEAM_ID", "Steam ID is not set"),
    ("CSA_LIVE_AUTH_CODE", "game authentication code is not set"),
    ("CSA_LIVE_KNOWN_CODE", "known share code is not set"),
])
def test_live_one_missing_prerequisite_is_named(missing, needle):
    code, out = run([], env=live_env(**{missing: None}))
    assert code == 2
    assert needle in out
    assert out.count("\n      - ") == 1  # only that one


@pytest.mark.parametrize("override, needle", [
    ({"CSA_LIVE_STEAM_ID": "12345"}, "Steam ID must be a 17-digit SteamID64"),
    ({"CSA_LIVE_AUTH_CODE": "nope"}, "game authentication code has the wrong format"),
    ({"CSA_LIVE_KNOWN_CODE": "CSGO-123"}, "known share code has the wrong format"),
    ({"TOKEN_ENCRYPTION_KEYS": "not-a-fernet-key"}, "TOKEN_ENCRYPTION_KEYS contains an invalid Fernet key"),
    ({"STEAM_BOT_REFRESH_TOKEN": None, "STEAM_BOT_USERNAME": "bot", "STEAM_BOT_PASSWORD": "pw"},
     "demo bot credentials are not set"),
    ({"STEAM_BOT_REFRESH_TOKEN": None, "STEAM_BOT_REFRESH_TOKEN_FILE": "/nonexistent/token"},
     "STEAM_BOT_REFRESH_TOKEN_FILE could not be read"),
    ({"MODEL_PATH": "/nonexistent/model.pkl"}, "model artifact not found"),
])
def test_live_invalid_prerequisites(override, needle):
    code, out = run([], env=live_env(**override))
    assert code == 2
    assert needle in out


def test_auth_code_file(tmp_path, demo_file):
    missing = tmp_path / "nope"
    code, out = run(["--auth-code-file", str(missing)], env=live_env(CSA_LIVE_AUTH_CODE=None))
    assert code == 2 and f"--auth-code-file: file {missing} could not be read" in out

    path = tmp_path / "auth"
    path.write_text(FAKE_AUTH_CODE.lower() + "\n")
    doubles, _, _ = live_doubles(demo_file)
    code, out = run(["--auth-code-file", str(path)], env=live_env(CSA_LIVE_AUTH_CODE=None), **doubles)
    assert code == 0, out


def test_auth_code_argument_warns_about_shell_history(demo_file):
    doubles, _, _ = live_doubles(demo_file)
    code, out = run(["--auth-code", FAKE_AUTH_CODE], env=live_env(CSA_LIVE_AUTH_CODE=None), **doubles)
    assert code == 0, out
    assert "ends up in shell history" in out


# --- the live code path with injected doubles ---------------------------------------

def test_live_path_passes_and_never_prints_secrets(demo_file):
    doubles, valve, gc = live_doubles(demo_file)
    code, out = run([], env=live_env(DATABASE_URL="postgresql://u:pw@prod/db"), **doubles)
    assert code == 0, out
    assert "LIVE: real Steam" in out and "RESULT: PASS (7/7 steps, LIVE" in out
    assert "DATABASE_URL from the environment is ignored" in out
    assert gc.closed
    for secret in (FAKE_API_KEY, REFRESH_TOKEN, FAKE_AUTH_CODE, FERNET, fake_codes(3)[0], "pw@prod"):
        assert secret not in out
    # history walk x3 + the sync request's own history call; one demo download
    paths = valve.requests
    assert paths.count("api.steampowered.com/ICSGOPlayers_730/GetNextMatchSharingCode/v1") == 4
    assert sum(p.startswith("replay1.valve.net/730/") for p in paths) == 1


def test_live_rejected_api_key(demo_file):
    doubles, _, _ = live_doubles(demo_file)
    code, out = run([], env=live_env(STEAM_WEB_API_KEY="wrong"), **doubles)
    assert code == 1
    assert "Steam rejected STEAM_WEB_API_KEY (HTTP 403)" in out
    assert statuses(out)[2] == "FAIL" and statuses(out)[3] == "SKIP"


def test_live_unknown_steam_id(demo_file):
    doubles, _, _ = live_doubles(demo_file)
    code, out = run([], env=live_env(CSA_LIVE_STEAM_ID="76561198000000999"), **doubles)
    assert code == 1 and "was not found (wrong SteamID64?)" in out


def test_live_rejected_known_code(demo_file):
    doubles, _, _ = live_doubles(demo_file)
    code, out = run(["--known-code", fake_codes(50)[40]], env=live_env(), **doubles)
    assert code == 1
    assert "Valve rejected the known share code (HTTP 412)" in out


def test_live_known_code_is_newest_match(demo_file):
    codes = fake_codes(3)
    doubles, _, _ = live_doubles(demo_file)
    code, out = run(["--known-code", codes[-1]], env=live_env(), **doubles)
    assert code == 0, out
    assert "no newer match than the known code" in out
    assert "sync:" not in out  # job queued directly


@pytest.mark.parametrize("gc, needle", [
    (GC(error=GCAuthError()), "Steam rejected the demo bot's login"),
    (GC(error=GCNotReady()), "did not reach the CS2 Game Coordinator within 60s"),
    (GC(error=GCTimeout()), "the Game Coordinator did not answer for this match"),
    (GC(result="none"), "the Game Coordinator knows no such match"),
    (GC(result="no_url"), "has the match but no demo URL"),
    (GC(result="bad_url"), "not a Valve replay URL"),
])
def test_live_gc_failures(demo_file, gc, needle):
    doubles, _, _ = live_doubles(demo_file, gc=gc)
    code, out = run([], env=live_env(), **doubles)
    assert code == 1
    assert needle in out
    assert statuses(out)[4] == "FAIL" and statuses(out)[5] == "SKIP"
    assert gc.closed


def test_live_download_404(demo_file):
    class Gone(FakeValve):
        def handler(self, request):
            if request.url.host.endswith(".valve.net"):
                import httpx
                return httpx.Response(404)
            return super().handler(request)

    valve = Gone(fake_codes(3), demo=demo_file)
    doubles, _, _ = live_doubles(demo_file, valve=valve)
    code, out = run([], env=live_env(), **doubles)
    assert code == 1
    assert "the replay host answered 404" in out


def test_live_download_too_large(demo_file):
    doubles, _, _ = live_doubles(demo_file)
    code, out = run([], env=live_env(DEMO_MAX_DOWNLOAD_BYTES="1000"), **doubles)
    assert code == 1
    assert "larger than DEMO_MAX_DOWNLOAD_BYTES" in out


def test_live_not_a_demo(tmp_path):
    junk = tmp_path / "junk"
    junk.write_bytes(b"<html>not a demo</html>")
    doubles, _, _ = live_doubles(str(junk))
    code, out = run([], env=live_env(), **doubles)
    assert code == 1
    assert "neither a CS2 .dem nor a .bz2 archive" in out


def test_unexpected_error_shows_type_only(demo_file):
    class Boom(GameCoordinator):
        def full_match_info(self, *a):
            raise RuntimeError("https://api.example/?key=SECRET")

    doubles, _, _ = live_doubles(demo_file, gc=Boom())
    code, out = run([], env=live_env(), **doubles)
    assert code == 1
    assert "unexpected RuntimeError" in out and "SECRET" not in out


# --- storing into a real database -------------------------------------------------------

def _db_url(tmp_path):
    engine = make_test_engine(tmp_path, "live_check")
    return engine.url.render_as_string(hide_password=False)


def test_database_url_stores_match_and_restores_the_users_link(tmp_path, demo_file):
    from steamlink.crypto import AuthCodeCipher
    from steamlink.migrate import apply_migrations
    from steamlink.storage.sql import SqlStorage, make_engine

    url = _db_url(tmp_path)
    engine = make_engine(url)
    apply_migrations(engine)
    storage = SqlStorage(engine)
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    user = storage.get_or_create_user(FAKE_STEAM_ID, now)
    old_cipher = AuthCodeCipher([FERNET]).encrypt(FAKE_STEAM_ID, "OLDD-CODEE-1234")
    storage.set_match_access(user.id, ciphertext=old_cipher, last4="1234", cursor_share_code=fake_codes(9)[9], now=now)

    doubles, _, _ = live_doubles(demo_file)
    code, out = run(["--database-url", url], env=live_env(), **doubles)
    assert code == 0, out
    assert "(new," in out
    matches = storage.list_matches(user.id, limit=10, offset=0)
    assert len(matches) == 1 and matches[0].status == "imported"
    access = storage.get_match_access(user.id)
    assert access.auth_code_ciphertext == old_cipher and access.cursor_share_code == fake_codes(9)[9]

    # Second run: the match is already stored -> the sync skips it, the job dedupes, no new match.
    doubles, _, _ = live_doubles(demo_file)
    code, out = run(["--database-url", url], env=live_env(), **doubles)
    assert code == 0, out
    assert "skipped=1" in out and "already stored" in out
    assert len(storage.list_matches(user.id, limit=10, offset=0)) == 1
    engine.dispose()


def test_database_url_without_previous_link_removes_it(tmp_path, demo_file):
    from steamlink.storage.sql import SqlStorage, make_engine

    url = _db_url(tmp_path)
    doubles, _, _ = live_doubles(demo_file)
    code, out = run([], env=live_env(CSA_LIVE_DATABASE_URL=url), **doubles)
    assert code == 0, out
    engine = make_engine(url)
    storage = SqlStorage(engine)
    from datetime import datetime, timezone

    user = storage.get_or_create_user(FAKE_STEAM_ID, datetime.now(timezone.utc))
    assert storage.get_match_access(user.id) is None
    assert len(storage.list_matches(user.id, limit=10, offset=0)) == 1
    engine.dispose()
