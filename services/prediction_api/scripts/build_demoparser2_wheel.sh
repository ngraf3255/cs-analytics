#!/usr/bin/env bash
# Rebuild the vendored demoparser2 wheel (vendor/wheels/README.md).
# Needs: git, a Rust toolchain (cargo), a CPython 3.11 interpreter.
#   PYTHON311=/path/to/python3.11 scripts/build_demoparser2_wheel.sh
set -euo pipefail

COMMIT=45ca85aeac8fb0de9d385124d2c7fe0e7b8ff0c8  # LaihoE/demoparser master, 2026-10-05 (#361, #363, #364)
VERSION=0.42.1.dev20261006
PYTHON311=${PYTHON311:-python3.11}
HERE=$(cd "$(dirname "$0")/.." && pwd)
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

git clone -q https://github.com/LaihoE/demoparser.git "$WORK/demoparser"
cd "$WORK/demoparser"
git checkout -q "$COMMIT"
# Soft-skip failed PacketEntities updates so Rush (rush_001) demos still yield events.
patch -p1 < "$HERE/vendor/patches/demoparser-packetents-softskip.patch"
# csgoproto's build.rs clones the *latest* SteamDatabase/GameTracking-CS2 protos and
# regenerates src/protobuf.rs on every build, so a build is not reproducible and can
# break whenever Valve renames messages. Use the checked-in generated code instead.
echo 'fn main() {}' > src/csgoproto/build.rs
sed -i "s/^dynamic = \[\"version\"\]/version = \"$VERSION\"/" src/python/pyproject.toml

python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" -q install 'maturin>=1.7,<2' ziglang
cd src/python
PATH="$WORK/venv/bin:$PATH" maturin build --release --zig --compatibility manylinux2014 \
  -i "$PYTHON311" -o "$HERE/vendor/wheels"
sha256sum "$HERE"/vendor/wheels/demoparser2-*.whl
