# Vendored wheels

## demoparser2 0.42.1.dev20261006 (CPython 3.11, manylinux2014 x86_64)

`demoparser2-0.42.1.dev20261006-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`
sha256 `PENDING_CI` — replace with the artifact from the workflow_dispatch run below.

**CI build:** PENDING (workflow_dispatch of `.github/workflows/build-demoparser2-wheel.yml` on this branch).
The committed wheel must be that run's artifact (byte-identical); paste the run URL and
`sha256` from the job summary here after the run finishes.

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

**Remove** when demoparser2 >= 0.42.1 is on PyPI *and* handles Rush PacketEntities: delete
this wheel + patch, drop the `--find-links` and both `demoparser2` lines in
`requirements.txt`, pin the release.
