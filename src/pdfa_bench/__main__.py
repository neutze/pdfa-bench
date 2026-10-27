"""pdfa-bench: python -m pdfa_bench [run|selftest|list] [--lanes P3,P4] [--modes triage]

Imports stay inside the branches: the self-test points the output folder at a temporary directory
before anything reads the configuration.
"""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pdfa-bench", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", nargs="?", default="run", choices=("run", "selftest", "list"))
    parser.add_argument("--lanes", default="", help="comma-separated lanes, default all")
    parser.add_argument("--modes", default="naive,triage", help="naive, triage or both")
    args = parser.parse_args()

    if args.command == "selftest":
        from . import selftest

        selftest.main()
        return

    from . import bench, lanes, report, vera
    from .base import say

    if args.command == "list":
        for name, lane in lanes.LANES.items():
            missing = lane.needs()
            say(
                f"{name:4} {lane.role:10} {'ready' if not missing else 'missing ' + ', '.join(missing):28} {lane.label}"
            )
        say(f"veraPDF: {'ready' if vera.command() else 'missing'}")
        return

    chosen = [lane for lane in args.lanes.split(",") if lane] or None
    unknown = [lane for lane in chosen or [] if lane not in lanes.LANES]
    if unknown:
        sys.exit(f"unknown lanes: {', '.join(unknown)}")
    chosen_modes = [mode for mode in args.modes.split(",") if mode]
    unknown = [mode for mode in chosen_modes if mode not in lanes.MODES]
    if unknown:
        sys.exit(f"unknown modes: {', '.join(unknown)}")
    modes = tuple(chosen_modes) or lanes.MODES
    report.show(bench.run(chosen, modes))


if __name__ == "__main__":
    main()
