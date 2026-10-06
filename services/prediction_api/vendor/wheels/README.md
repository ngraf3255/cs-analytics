# Vendored wheels

## demoparser2 0.42.1.dev20261005 (CPython 3.11, manylinux2014 x86_64)

`demoparser2-0.42.1.dev20261005-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`
sha256 `f34e48ea59c8a1de2b00f52e8b382efcf39abd36e0cde9c393c6b6ea4e036659`

**Why:** demoparser2 0.42.0 (newest on PyPI as of 2026-10-06) stops with
`MalformedMessage` on current CS2 demos. Upstream master fixes it but hasn't released:

- LaihoE/demoparser#364: a game event with non-UTF-8 bytes in a string value made prost
  reject the whole `CSVCMsg_GameEvent` (`MalformedMessage`). Now decoded lossily.
- LaihoE/demoparser#363 (fixes #362): entity handles were masked to 11 bits, so players
  with pawn index > 2047 got all-null props.
- LaihoE/demoparser#361: current Valve protos made `customnames` repeated; fresh builds broke.

**What:** upstream commit `45ca85aeac8fb0de9d385124d2c7fe0e7b8ff0c8` (master, 2026-10-05), unmodified Rust code, built
with `csgoproto/build.rs` disabled so it uses the checked-in generated protobuf code instead
of cloning the latest GameTracking-CS2 protos at build time (unpinned, not reproducible).
Version set to `0.42.1.dev20261005` so it sorts below the real 0.42.1.

**Rebuild:** `scripts/build_demoparser2_wheel.sh` (Rust + CPython 3.11; uses zig for the
manylinux2014 / glibc 2.17 baseline). Rust builds are not byte-reproducible, so a rebuild has a
different sha256; re-run the verification below before swapping it in.

**Verified (2026-10-06):** full API test suite (423 passed) and `tests/test_real_demo.py` on
the demoparser fixture, a Valve MM replay (de_ancient), a FACEIT demo (de_mirage, 25 rounds)
and an HLTV demo (de_nuke, 18 rounds); upstream unit tests for #363/#364 pass.

**Remove** when demoparser2 >= 0.42.1 is on PyPI: delete this wheel, drop the
`--find-links` and both `demoparser2` lines in `requirements.txt`, pin the release.
