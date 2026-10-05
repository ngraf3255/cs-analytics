"""Memory budget for the upload path on a REAL demo (Render's free/starter instances have 512 MB).

Opt-in like test_real_demo.py: set CSA_TEST_DEMO to a CS2 .dem. Runs
scripts/measure_demo_memory.py in a fresh process (model loaded like the
server, then upload -> parse -> store -> score) and fails if

* peak anonymous memory (API process + parse child) exceeds
  CSA_TEST_DEMO_ANON_BUDGET_MB (default 300; measured 2026-10-05: ~232 MB on a
  441 MB / 18-round HLTV demo, ~228 MB on a 372 MB / 25-round FACEIT demo,
  ~214 MB on the 58 MB demoparser2 fixture), or
* the API process keeps more than 30 MB of extra heap after the import
  (parsing runs in a child process whose memory goes back to the OS).

File-backed memory (the memory-mapped demo) is excluded from the budget: those
are clean pages the kernel reclaims under pressure; see the script's docstring.
"""

import json
import os
import subprocess
import sys

import pytest

DEMO = os.environ.get("CSA_TEST_DEMO")
BUDGET_MB = float(os.environ.get("CSA_TEST_DEMO_ANON_BUDGET_MB", "300"))
pytestmark = [
    pytest.mark.skipif(not DEMO or not os.path.isfile(DEMO), reason="set CSA_TEST_DEMO to a CS2 demo"),
    pytest.mark.skipif(not os.path.exists("/proc/self/smaps_rollup"), reason="needs Linux /proc"),
]
SCRIPT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts", "measure_demo_memory.py")


def test_upload_path_stays_within_memory_budget():
    env = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "TOKEN_ENCRYPTION_KEYS")}
    done = subprocess.run(
        [sys.executable, SCRIPT, DEMO, "--json", "--budget-mb", str(BUDGET_MB), "--max-retained-mb", "30"],
        capture_output=True, text=True, timeout=900, env=env,
    )
    report = json.loads(done.stdout.strip().splitlines()[-1])
    assert done.returncode == 0, (report, done.stderr[-2000:])
    assert report["upload_rejected"] is None and report["rounds"] > 0
    assert report["peak_anon_mb"] <= BUDGET_MB
