"""Record a human decision against a paused run. OWNER: Sorour.

    python -m agentorg.gates_cli list
    python -m agentorg.gates_cli resume <run_id> --gate gate1 \
        --decision approved --by sorour --reason "plan looks right"

The week-3 UI is buttons over exactly these calls.

This is the ASYNC half of the gate story. `graph._cli_gate` is the synchronous
half: it asks on the terminal and blocks. Both go through `gates.pause`, so a
run abandoned at a terminal prompt is not lost — the state file is already on
disk and whoever picks it up later resumes it from here.
"""

import argparse
import pathlib

from . import gates
from .state import HumanDecision

_RUNS = pathlib.Path(__file__).resolve().parent.parent / "runs"


def _list() -> None:
    for path in sorted(_RUNS.glob("*.state.json")):
        print(path.name.removesuffix(".state.json"))


def _resume(args: argparse.Namespace) -> None:
    decision = HumanDecision(gate=args.gate, decision=args.decision,
                             by=args.by, reason=args.reason)
    state = gates.resume(args.run_id, decision)
    print(f"run_id={state.run_id} gate={args.gate} "
          f"decision={args.decision} status={state.status}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="agentorg.gates_cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    resume = sub.add_parser("resume")
    resume.add_argument("run_id")
    resume.add_argument("--gate", required=True,
                        choices=["gate1", "gate2", "gate3"])
    resume.add_argument("--decision", required=True,
                        choices=["approved", "rejected", "overridden"])
    resume.add_argument("--by", required=True)
    resume.add_argument("--reason", default="")
    args = parser.parse_args()
    if args.cmd == "list":
        _list()
    else:
        _resume(args)


if __name__ == "__main__":
    main()
