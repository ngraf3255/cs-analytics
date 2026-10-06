# Vendored wheels

## demoparser2 0.42.1.dev20261005 (CPython 3.11, manylinux2014 x86_64)

`demoparser2-0.42.1.dev20261005-cp311-cp311-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`
sha256 `63f5c6c7950c2715dbc79fe1073db7e45eb552098ccf0091f0d08b2ab2193230` (also in `SHA256SUMS`)

**Built by CI, not by hand:** this file is the `demoparser2-wheel` artifact of workflow run
https://github.com/ngraf3255/cs-analytics/actions/runs/37421365078 (`workflow_dispatch` of
`.github/workflows/build-demoparser2-wheel.yml` at repo commit `9f426e8`, ubuntu-24.04,
rustc 1.99.0, CPython 3.11.16). The artifact holds the wheel, its `.sha256` and
`build-info.txt` (includes `auditwheel show`: consistent with `manylinux_2_17_x86_64`) and is
kept until 2027-01-04. To check: download it (`gh run download 37421365078 -n demoparser2-wheel`)
and compare its sha256 with the one above, or run `sha256sum -c SHA256SUMS` here.

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

**Rebuild:** run the "Build demoparser2 wheel" workflow (Actions -> Run workflow, or
`gh workflow run build-demoparser2-wheel.yml --ref <branch>`). It runs
`scripts/build_demoparser2_wheel.sh` (Rust + CPython 3.11; uses zig for the manylinux2014 /
glibc 2.17 baseline), uploads the artifact, then installs that exact wheel binary-only through
`requirements.txt` (asserting the sha256 pip installed), runs the full API suite with the
demoparser fixture as `CSA_TEST_DEMO`, and `tests/test_real_demo.py` on an extra real demo
(`demo_url` input; default: the awpy Valve MM de_ancient demo). Rust builds are not
byte-reproducible, so every run has a different sha256: commit the artifact's wheel, and update
the sha256, `SHA256SUMS` and run URL here. The workflow also runs on PRs that change the recipe
(those artifacts are only build checks, not the vendored file).

**Verified in run 37421365078 (this exact wheel):** pip installed it from `vendor/wheels`
with the sha256 above; full API suite 430 passed, 3 skipped (demoparser fixture de_mirage);
`tests/test_real_demo.py` 5 passed on the awpy Valve MM de_ancient demo (sha256 `b29a9cb5…`).

**Verified earlier on the hand-built wheel (2026-10-06, same commit and recipe):** full API
test suite (423 passed) and `tests/test_real_demo.py` on the demoparser fixture, a Valve MM replay (de_ancient), a FACEIT demo (de_mirage, 25 rounds)
and an HLTV demo (de_nuke, 18 rounds); upstream unit tests for #363/#364 pass.

**Remove** when demoparser2 >= 0.42.1 is on PyPI: delete this wheel and
`SHA256SUMS`, drop the `--find-links` and both `demoparser2` lines in `requirements.txt`, pin the
release, and delete `.github/workflows/build-demoparser2-wheel.yml` and the build script.
