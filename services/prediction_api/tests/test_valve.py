import bz2
import os

import httpx
import pytest

from steamlink import sharecode
from steamlink.valve import (
    DemoFetcher, DemoNotReady, DemoTooLarge, DemoUnavailable, SteamWebMatchHistoryClient, check_replay_url, is_valid_auth_code,
)

KNOWN = "CSGO-GADqf-jjyJ8-cSP2r-smZRo-TO2xK"
GOOD_URL = "http://replay183.valve.net/730/3230642215713767580_3230647599455273103.dem.bz2"


def test_share_code_decode_known_vector():
    decoded = sharecode.decode(KNOWN)
    assert decoded == sharecode.ShareCode(3230642215713767580, 3230647599455273103, 55788)
    assert sharecode.encode(decoded) == KNOWN


@pytest.mark.parametrize("bad", ["", "CSGO-GADqf-jjyJ8-cSP2r-smZRo", "CSGO-GADq0-jjyJ8-cSP2r-smZRo-TO2xK", "csgo-x"])
def test_share_code_rejects_invalid(bad):
    with pytest.raises(sharecode.InvalidShareCode):
        sharecode.decode(bad)


def test_share_code_roundtrip_random():
    share = sharecode.ShareCode(2**63 + 12345, 987654321, 65535)
    assert sharecode.decode(sharecode.encode(share)) == share


def test_auth_code_format():
    assert is_valid_auth_code("AB12-CDE34-FG56")
    assert not is_valid_auth_code("ab12-cde34-fg56")
    assert not is_valid_auth_code("AB12CDE34FG56")


def _client(status, body):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(status, json=body)

    return SteamWebMatchHistoryClient("KEY", httpx.Client(transport=httpx.MockTransport(handler))), seen


@pytest.mark.parametrize("status,body,expected", [
    (200, {"result": {"nextcode": "CSGO-AAAAA-BBBBB-CCCCC-DDDDD-EEEEE"}}, "ok"),
    (202, {"result": {"nextcode": "n/a"}}, "no_new_match"),
    (200, {"result": {"nextcode": "n/a"}}, "no_new_match"),
    (403, {}, "invalid_auth_code"),
    (412, {}, "invalid_known_code"),
    (429, {}, "rate_limited"),
    (500, {}, "valve_error"),
    (200, {"result": {"nextcode": "garbage"}}, "valve_error"),
])
def test_next_share_code_status_mapping(status, body, expected):
    client, seen = _client(status, body)
    result = client.next_share_code("76561198000000001", "AB12-CDE34-FG56", KNOWN)
    assert result.status == expected
    assert seen[0].url.params["steamidkey"] == "AB12-CDE34-FG56"
    assert seen[0].url.host == "api.steampowered.com"


def test_httpx_request_logging_is_silenced():
    import logging
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING


def test_replay_url_allowlist_accepts_padded_match_id():
    check_replay_url("http://replay382.valve.net/730/003767418281950970048_1750155669.dem.bz2")
    check_replay_url("https://replay1.valve.net/730/3230642215713767580_3230647599455273103.dem.bz2")


@pytest.mark.parametrize("url", ["http://replay1.valve.net/730/1_2.dem", "http://replay1.valve.net/570/1_2.dem.bz2"])
def test_replay_url_allowlist_rejects_wrong_path(url):
    with pytest.raises(ValueError):
        check_replay_url(url)


@pytest.mark.parametrize("url", [
    "http://evil.com/730/1_2.dem.bz2",
    "http://replay1.valve.net.evil.com/730/1_2.dem.bz2",
    "http://169.254.169.254/730/1_2.dem.bz2",
    "http://replay1.valve.net/730/1_2.dem.bz2?x=1",
    "http://replay1.valve.net:8080/730/1_2.dem.bz2",
    "http://user@replay1.valve.net/730/1_2.dem.bz2",
    "file:///etc/passwd",
    "http://replay1.valve.net/../etc/passwd",
])
def test_replay_url_allowlist_rejects(url):
    with pytest.raises(ValueError):
        check_replay_url(url)


def _fetcher(payload, status=200, **limits):
    def handler(request):
        return httpx.Response(status, content=payload)

    opts = {"max_download_bytes": 10_000_000, "max_decompressed_bytes": 10_000_000, **limits}
    return DemoFetcher(httpx.Client(transport=httpx.MockTransport(handler)), **opts)


def test_fetch_decompresses_and_cleans_up():
    raw = b"HL2DEMO" + os.urandom(1000) * 50
    with _fetcher(bz2.compress(raw)).fetch(GOOD_URL) as path:
        with open(path, "rb") as fh:
            assert fh.read() == raw
        workdir = os.path.dirname(path)
        assert os.listdir(workdir) == ["demo.dem"]
    assert not os.path.exists(workdir)


def test_fetch_cleans_up_when_parsing_raises():
    with pytest.raises(RuntimeError):
        with _fetcher(bz2.compress(b"x" * 100)).fetch(GOOD_URL) as path:
            workdir = os.path.dirname(path)
            raise RuntimeError("parser blew up")
    assert not os.path.exists(workdir)


def test_fetch_enforces_download_limit():
    with pytest.raises(DemoTooLarge):
        with _fetcher(b"x" * 5000, max_download_bytes=1000).fetch(GOOD_URL):
            pass


def test_fetch_enforces_decompressed_limit():
    with pytest.raises(DemoTooLarge):
        with _fetcher(bz2.compress(b"\0" * 5_000_000), max_decompressed_bytes=1_000_000).fetch(GOOD_URL):
            pass


def test_fetch_404_is_unavailable():
    with pytest.raises(DemoUnavailable):
        with _fetcher(b"", status=404).fetch(GOOD_URL):
            pass


def test_download_reports_progress_and_keeps_the_compressed_file(tmp_path):
    payload = bz2.compress(os.urandom(300_000))
    dest = tmp_path / "job.upload"
    seen = []
    _fetcher(payload).download(GOOD_URL, str(dest), on_progress=seen.append)
    assert dest.read_bytes() == payload  # the job pipeline decompresses (with its own progress)
    assert seen and seen[-1] == 1.0 and seen == sorted(seen)


@pytest.mark.parametrize("payload,status,limits,error", [
    (b"x" * 5000, 200, {"max_download_bytes": 1000}, DemoTooLarge),
    (b"", 404, {}, DemoUnavailable),
    (b"", 503, {}, DemoNotReady),
])
def test_download_errors_remove_the_partial_file(tmp_path, payload, status, limits, error):
    dest = tmp_path / "job.upload"
    with pytest.raises(error):
        _fetcher(payload, status=status, **limits).download(GOOD_URL, str(dest))
    assert not dest.exists()


def test_download_only_from_valve_replay_hosts(tmp_path):
    with pytest.raises(ValueError):
        _fetcher(b"x").download("http://169.254.169.254/730/1_2.dem.bz2", str(tmp_path / "x"))
