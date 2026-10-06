# Vendored wheels

## demoparser2 0.42.1.dev20261006 (CPython 3.11, manylinux2014 x86_64)

`demoparser2-0.42.1.dev20261006-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`
sha256 `1a764b9199b0d38be5e19e44990d9335a8b3f48b22d149e4bfb0bbc3c9bb86b0`

**CI build:** https://github.com/ngraf3255/cs-analytics/actions/runs/37423283870 (workflow_dispatch on `fix/demoparser2-rush-packetents-softskip`).
This file is that run's artifact (byte-identical).

**Why:** demoparser2 0.42.0 (newest on PyPI as of 2026-10-06) stops with
`MalformedMessage` on current CS2 demos. Upstream master fixes the common cases but
hasn't released, and Valve Rush-mode demos (`rush_001`) still abort mid-parse:

- LaihoE/demoparser#364: a game event with non-UTF-8 bytes in a string value made prost
  reject the whole `CSVCMsg_GameEvent` (`MalformedMessage`). Now decoded lossily.
- LaihoE/demoparser#363 (fixes #362): entity handles were masked to 11 bits, so players
  with pawn index > 2047 got all-null props.
- LaihoE/demoparser#361: current Valve protos made `customnames` repeated; fresh builds broke.
- **Rush / PacketEntities (local patch):** on `rush_001`, `svc_PacketEntities` updates raise
  `MalformedMessage` (first) then cascading `EntityNotFound` and abort the second pass before
  game events are collected. Soft-skip **only** those two variants for the failed PacketEntities
  message (not every `Err`), count the skips, and expose them via `demoparser2.packet_ents_skips()`
  so the API marks the match `status_reason=parse_degraded` / UI `DEGRADED`. Competitive demos
  that already parse cleanly are unchanged (the soft-skip only runs on those two errors).

**What:** upstream commit `45ca85aeac8fb0de9d385124d2c7fe0e7b8ff0c8` (master, 2026-10-05),
plus `vendor/patches/demoparser-packetents-softskip.patch`, built with `csgoproto/build.rs`
disabled so it uses the checked-in generated protobuf code instead of cloning the latest
GameTracking-CS2 protos at build time (unpinned, not reproducible). Version set to
`0.42.1.dev20261006` so it sorts below the real 0.42.1.

**Rebuild:** `scripts/build_demoparser2_wheel.sh` (Rust + CPython 3.11; uses zig for the
manylinux2014 / glibc 2.17 baseline). Prefer the Actions workflow
(`workflow_dispatch`) so the wheel is CI-traceable. Rust builds are not byte-reproducible,
so a rebuild has a different sha256; re-run the verification before swapping it in.

**Verified (2026-10-06):** workflow_dispatch run above (pytest green + real demo parse).
Locally, Noah's Valve Rush GOTV demo (`rush_001`) yields 15 deaths / 1 round_end with
degraded PacketEntities skips; demoparser fixture still parses cleanly.

**Rush follow-up (Python, PARSE_VERSION 4):** soft-skip still leaves ~9.7k PacketEntities
skips on Noah's `rush_001` GOTV demo — positions/props stay thin and the match stays
`parse_degraded`. The API now also reads `round_officially_ended`: when those outnumber
`round_end`, rounds come from freeze → officially-ended with winners from the observed
score delta, and blank death/spawn `team_num` is recovered from the same SteamID's later
observations. Incomplete trailing freezes without officially-ended are dropped. No new
wheel required for that recovery; a finer per-entity soft-skip (if ever) would be a
separate demoparser patch + Homelab wheel bump.


**Remove** when demoparser2 >= 0.42.1 is on PyPI *and* handles Rush PacketEntities: delete
this wheel + patch, drop the `--find-links` and both `demoparser2` lines in
`requirements.txt`, pin the release.
