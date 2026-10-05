"""Measure peak memory of the upload path (stream-to-disk -> .bz2 -> parse -> store -> score) on a real demo.

Runs in a fresh process that first imports ``main`` (model loaded, like the
server at idle), then imports the given demo exactly as ``POST /matches/upload``
does (``import_uploaded_demo`` with the production parser and a throwaway
SQLite database) and scores the rounds as ``GET /matches/{id}`` does.

Memory is sampled every few ms over this process plus all of its child
processes (so a parse subprocess counts):

* ``peak_anon_mb``: anonymous memory (heap). Not reclaimable without swap; this
  is what gets a 512 MB instance OOM-killed.
* ``peak_pss_mb``: total proportional set size, i.e. anon + file-backed pages
  (shared libraries and the memory-mapped demo). demoparser2 mmaps the whole
  .dem, so this grows with demo size, but those are clean file pages the kernel
  drops under memory pressure.

To check a real limit, run it inside a cgroup v2 with ``memory.max=512M`` and
``memory.swap.max=0`` and look at ``memory.events`` (``oom_kill``). Results
from 2026-10-05 are in docs/steam-match-sync-plan.md (Status).

Usage (from services/prediction_api)::

    python scripts/measure_demo_memory.py /path/to/demo.dem[.bz2] \
        [--budget-mb 300] [--max-retained-mb 30] [--isolation inprocess] [--json]

Exit status 1 if ``--budget-mb`` is given and the peak anonymous memory exceeds
it, or ``--max-retained-mb`` is given and the API process keeps more heap than
that after the import.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _status(pid: int) -> dict[str, int]:
    """RssAnon (private heap) from /proc/<pid>/status; Pss/Pss_Anon/Pss_File from smaps_rollup.

    PSS splits shared pages (Python, pandas, ... shared libraries) between the
    processes mapping them, so parent + child totals don't double count.
    """
    out: dict[str, int] = {}
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                key, _, value = line.partition(":")
                if key in ("VmRSS", "RssAnon"):
                    out[key] = int(value.split()[0]) * 1024
        with open(f"/proc/{pid}/smaps_rollup") as fh:
            for line in fh:
                key, _, value = line.partition(":")
                if key in ("Pss", "Pss_Anon", "Pss_File"):
                    out[key] = int(value.split()[0]) * 1024
    except (FileNotFoundError, ProcessLookupError):
        pass
    return out


def _descendants(pid: int) -> list[int]:
    found, stack = [], [pid]
    while stack:
        current = stack.pop()
        try:
            for task in os.listdir(f"/proc/{current}/task"):
                with open(f"/proc/{current}/task/{task}/children") as fh:
                    kids = [int(k) for k in fh.read().split()]
                found.extend(kids)
                stack.extend(kids)
        except (FileNotFoundError, ProcessLookupError):
            continue
    return found


class PeakSampler:
    def __init__(self, interval: float = 0.003):
        self.interval = interval
        self.peak_total = self.peak_anon = 0
        self.peak_at = ""
        self._stage = "idle"
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def stage(self, name: str) -> None:
        self._stage = name

    def sample(self) -> tuple[int, int]:
        pids = [os.getpid(), *_descendants(os.getpid())]
        stats = [_status(p) for p in pids]
        total = sum(s.get("Pss", 0) for s in stats)
        anon = sum(s.get("RssAnon", 0) for s in stats)
        if total > self.peak_total:
            self.peak_total, self.peak_at = total, self._stage
        self.peak_anon = max(self.peak_anon, anon)
        return total, anon

    def _run(self) -> None:
        while not self._stop.is_set():
            self.sample()
            time.sleep(self.interval)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join()
        self.sample()


def _cpu_seconds() -> float:
    total = 0.0
    for who in (resource.RUSAGE_SELF, resource.RUSAGE_CHILDREN):
        usage = resource.getrusage(who)
        total += usage.ru_utime + usage.ru_stime
    return total


def measure(demo: str, isolation: str = "subprocess") -> dict:
    import main  # noqa: F401  (loads the model like the server)
    from steamlink.demo_parser import Demoparser2Parser
    from steamlink.migrate import apply_migrations
    from steamlink.storage.sql import SqlStorage, make_engine
    from steamlink.upload import UploadRejected, import_uploaded_demo

    workdir = tempfile.mkdtemp(prefix="csa-memtest-")
    try:
        storage = SqlStorage(make_engine(f"sqlite:///{workdir}/memtest.db"))
        apply_migrations(storage.engine)
        now = datetime.now(timezone.utc)
        user = storage.get_or_create_user("76561198000000001", now)
        sampler = PeakSampler()
        idle_total, idle_anon = sampler.sample()
        sampler.peak_total = sampler.peak_anon = 0
        started = time.monotonic()
        cpu_before = _cpu_seconds()
        with sampler:
            sampler.stage("upload_body")
            upload_dir = os.path.join(workdir, "upload")
            os.mkdir(upload_dir)
            raw_path = os.path.join(upload_dir, "upload.bin")
            with open(demo, "rb") as src, open(raw_path, "wb") as out:  # the endpoint streams the body to disk
                while chunk := src.read(64 * 1024):
                    out.write(chunk)
            sampler.stage("import(parse+store)")
            try:
                parser = Demoparser2Parser(isolation=isolation)
            except TypeError:  # older code without isolation (before/after comparisons)
                parser = Demoparser2Parser()
            rejected = match = None
            rounds, scores = [], []
            try:
                result = import_uploaded_demo(
                    storage=storage, parser=parser, user=user, raw_path=raw_path, workdir=upload_dir,
                    max_compressed_bytes=1 << 40, max_demo_bytes=1 << 40, now=now,
                )
            except UploadRejected as exc:  # e.g. demo_parse_failed when the parse child was OOM-killed
                rejected = exc.reason
            else:
                sampler.stage("report(score)")
                match, rounds = storage.get_match(user.id, result.match_id)
                scores = main.scorer.score_rounds(match.map_name, rounds)
        after_total, after_anon = sampler.sample()
        mb = 1024 * 1024
        return {
            "demo": demo, "isolation": isolation, "demo_mb": round(os.path.getsize(demo) / mb, 1),
            "upload_rejected": rejected,
            "map": match.map_name if match else None, "rounds": len(rounds),
            "scored": sum(s.unscored_reason is None for s in scores),
            "seconds": round(time.monotonic() - started, 2),
            "cpu_seconds": round(_cpu_seconds() - cpu_before, 2),
            "idle_pss_mb": round(idle_total / mb), "idle_anon_mb": round(idle_anon / mb),
            "peak_pss_mb": round(sampler.peak_total / mb), "peak_anon_mb": round(sampler.peak_anon / mb),
            "peak_stage": sampler.peak_at,
            "after_pss_mb": round(after_total / mb), "after_anon_mb": round(after_anon / mb),
            "first_rounds": [(r.winner_side, r.opening_kill_side, r.opening_kill_seconds, r.opening_weapon,
                              r.unscored_reason) for r in rounds[:3]],
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main_cli() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("demo")
    ap.add_argument("--budget-mb", type=float, default=None,
                    help="fail if peak anonymous memory (API + parse child) exceeds this many MB")
    ap.add_argument("--max-retained-mb", type=float, default=None,
                    help="fail if anonymous memory after the import exceeds idle by more than this many MB")
    ap.add_argument("--isolation", choices=("subprocess", "inprocess"), default="subprocess")
    ap.add_argument("--json", action="store_true", help="print one JSON object only")
    args = ap.parse_args()
    report = measure(args.demo, args.isolation)
    if report["upload_rejected"]:
        print(f"upload rejected: {report['upload_rejected']}", file=sys.stderr)
    if args.json:
        print(json.dumps(report))
    else:
        for key, value in report.items():
            print(f"{key:>14}: {value}")
    if args.budget_mb is not None and report["peak_anon_mb"] > args.budget_mb:
        print(f"FAIL: peak anon {report['peak_anon_mb']} MB > budget {args.budget_mb} MB", file=sys.stderr)
        return 1
    retained = report["after_anon_mb"] - report["idle_anon_mb"]
    if args.max_retained_mb is not None and retained > args.max_retained_mb:
        print(f"FAIL: {retained} MB of heap retained after the import > {args.max_retained_mb} MB", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main_cli())
