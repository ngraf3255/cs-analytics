"""Child-process entry point for demo parsing: ``python -m steamlink.parse_worker <demo.dem>``.

Prints the parsed demo as one JSON object on stdout (see
``demo_parser.parsed_demo_to_json``); exits 2 if the demo can't be parsed.
Imports only the parser (not the API, model, or database code) so the child
stays small; all of its memory goes back to the OS when it exits.
"""

from __future__ import annotations

import json
import sys

from .demo_parser import DemoParseError, parse_in_process, parsed_demo_to_json


def _volunteer_for_oom_kill() -> None:
    """If memory runs out mid-parse, the kernel should kill this child, not the API.

    The OOM killer picks the largest process, which could be the API (it holds
    the model and libraries); oom_score_adj=1000 makes this child the first
    choice. Raising one's own score needs no privileges.
    """
    try:
        with open("/proc/self/oom_score_adj", "w") as fh:
            fh.write("1000")
    except OSError:  # not Linux, or /proc not writable: best effort
        pass


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: python -m steamlink.parse_worker <demo.dem>", file=sys.stderr)
        return 64
    _volunteer_for_oom_kill()
    try:
        demo = parse_in_process(argv[0])
    except DemoParseError as exc:
        print(f"{exc}: {exc.__cause__!r}", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(parsed_demo_to_json(demo), separators=(",", ":")))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
