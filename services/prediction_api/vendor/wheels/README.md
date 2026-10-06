# Vendored wheels

## demoparser2 0.42.1.dev20261006 (CPython 3.11, manylinux2014 x86_64)

`demoparser2-0.42.1.dev20261006-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`
sha256 `e1d2837eb2236fc8439b4392e65c86b7f53700587e8a6611b8625f9cb2ed46dc`

**Why:** demoparser2 0.42.0 (newest on PyPI as of 2026-10-06) stops with
`MalformedMessage` on current CS2 demos. Upstream master fixes the common cases but
hasn't released, and Valve Rush-mode demos (`rush_001`) still abort mid-parse:

- LaihoE/demoparser#364: a game event with non-UTF-8 bytes in a string value made prost
  reject the whole `CSVCMsg_GameEvent` (`MalformedMessage`). Now decoded lossily.
- LaihoE/demoparser#363 (fixes #362): entity handles were masked to 11 bits, so players
  with pawn index > 2047 got all-null props.
- LaihoE/demoparser#361: current Valve protos made `customnames` repeated; fresh builds broke.
- **Rush / PacketEntities (local patch):** on `rush_001` (and potentially other modes),
  `svc_PacketEntities` updates raise `EntityNotFound` / `MalformedMessage` and abort the
  whole second pass before game events are collected. Header / player_info / event-list
  still work. Soft-skipping a failed PacketEntities update lets `parse_events` continue so
  we can store rounds/deaths for scoring. Competitive demos that already parse cleanly are
  unchanged (the soft-skip only runs on `Err`).

**What:** upstream commit `45ca85aeac8fb0de9d385124d2c7fe0e7b8ff0c8` (master, 2026-10-05),
plus `vendor/patches/demoparser-packetents-softskip.patch`, built with `csgoproto/build.rs`
disabled so it uses the checked-in generated protobuf code instead of cloning the latest
GameTracking-CS2 protos at build time (unpinned, not reproducible). Version set to
`0.42.1.dev20261006` so it sorts below the real 0.42.1.

**Rebuild:** `scripts/build_demoparser2_wheel.sh` (Rust + CPython 3.11; uses zig for the
manylinux2014 / glibc 2.17 baseline). Rust builds are not byte-reproducible, so a rebuild has a
different sha256; re-run the verification below before swapping it in.

**Verified (2026-10-06):** Noah's Valve Rush GOTV demo (`rush_001`, patch 14188, 12.3 MB
`.dem` from `.dem.bz2`) parses via `parse_in_process` (15 deaths, 1 round) with this wheel;
demoparser fixture `test_demo.dem` still yields 73 player_death rows. Prior wheel
`0.42.1.dev20261005` failed the same Rush file with `MalformedMessage` on PacketEntities.

**Remove** when demoparser2 >= 0.42.1 is on PyPI *and* handles Rush PacketEntities: delete
this wheel + patch, drop the `--find-links` and both `demoparser2` lines in
`requirements.txt`, pin the release.
