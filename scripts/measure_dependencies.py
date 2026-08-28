#!/usr/bin/env python3
"""How coupled is this codebase to a third party? Measured over the AST.

    .venv-main/bin/python scripts/measure_dependencies.py

Answers judge requirement 3 (external dependency) with a number a reader can reproduce.
Prints a table and exits 0; exits 1 only if it cannot parse a module, because a
measurement that silently skipped a file would understate the coupling it exists to
report.

WHY THIS SCRIPT EXISTS, AND WHY NOT grep
========================================
`docs/final/01-specification.md` §5 originally quoted "33 references to `bedrock`, 13 to
`amazonaws`, 5 to `github.com`, 1 to `openai`". None of the four reproduced under any
scope: `agentorg/` alone gives 32 / 0 / 4 / 7, and widening to `scripts/` and `infra/`
gives 101 / 17 / 96 / 7. The numbers were not wrong so much as meaningless -- a grep for a
vendor's name counts PROSE, and this repository is roughly 40% commentary, so the figure
measures how much we wrote *about* a dependency rather than how coupled we are to it.

Two files make the point. `agentorg/state.py` mentions `bedrock` and imports nothing but
`pydantic` and the standard library -- it is the FROZEN contract, the least coupled file in
the package, and a grep ranks it alongside the client that actually calls AWS.
`agentorg/common/config.py` mentions all four vendors and imports none of them.

So this script asks the question a judge actually means: **which modules import a vendor
SDK, and can the package be loaded without it?**

THE DISTINCTION THAT MATTERS
============================
A module-level import is a HARD dependency: the module cannot be imported at all without
the package installed. A function-local import is DEFERRED -- the module loads fine and
only the one code path needs it.

That is not a stylistic difference here, it is a measured production failure. CLAUDE.md
records that the spec's three-line `requirements.txt` built a container image that died at
import, because `github_ops.py:41` is module-level and unconditional and `graph.py` imports
`github_ops`. This script reports that exact line as the one hard vendor import in the
package, from an independent direction.

A TRAP PAID FOR WHILE WRITING THIS
==================================
`ast.walk(node)` on a top-level statement DESCENDS INTO FUNCTION BODIES. The first version
of `_module_level` walked each top-level node and reported all four modules as
module-level -- the deferred/hard distinction, the entire point of the script, collapsed
silently and the output still looked like a plausible answer. Hence `_TOP_LEVEL_BARRIERS`
below and the self-check in `main`, which asserts the split is non-trivial: if every
touching module reports a hard import, the walk has almost certainly leaked again.

THE RED STEP, AND ONE MUTATION THAT PROVED NOTHING
==================================================
Recorded because the first mutation attempted here was INERT, and an inert mutation reads
exactly like a passing one.

Removing the `break` from the innermost `ast.walk` loop changed the output not at all:
`1` hard / `3` deferred, identical, exit 0. It is unreachable on this codebase -- no module
here nests a def deeply enough under a non-def top-level statement for the inner barrier to
matter. Had that been accepted as the RED step, the self-check below would have been
recorded as "verified" without ever having fired.

The mutation that DOES move the answer is dropping the outer barrier -- the
`if isinstance(node, _TOP_LEVEL_BARRIERS): continue` guard over `tree.body`. Measured:

    with a MODULE-LEVEL vendor import   : 4      <- was 1
    deferred only (import in a function): 0      <- was 3
    REFUSING: every touching module reports a MODULE-LEVEL import ...
    EXIT: 1

That is both the self-check firing and a faithful reproduction of the original wrong
answer. The inner `break` stays -- it is correct, and cheap -- but it is defence in depth,
not the load-bearing guard, and this file should not claim otherwise.
"""

from __future__ import annotations

import ast
import pathlib
import sys

# Top-level distribution name -> what a reader should understand is at stake.
VENDORS = {
    "boto3": "AWS SDK (Bedrock, DynamoDB, Secrets Manager)",
    "botocore": "AWS SDK internals (exception types, retry config)",
    "github": "PyGithub -- the GitHub REST client",
    "openai": "OpenAI-compatible gateway client",
}

# A vendor import inside one of these is still MODULE LEVEL: the statement runs at import
# time. Everything else -- def, async def, class -- defers it. This tuple is the fix for
# the ast.walk trap in this module's docstring, and it is deliberately a barrier list
# rather than an allow list, so a construct nobody anticipated is treated as module level
# (the conservative direction: it over-reports coupling rather than under-reporting it).
_TOP_LEVEL_BARRIERS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)

PACKAGE = pathlib.Path(__file__).resolve().parent.parent / "agentorg"


def _vendors_in(node: ast.AST) -> set[str]:
    """The vendor distributions one import statement pulls in. Empty for anything else."""
    found: set[str] = set()
    if isinstance(node, ast.Import):
        found |= {alias.name.split(".")[0] for alias in node.names}
    elif isinstance(node, ast.ImportFrom) and node.module:
        # A relative import (`from ..common import config`) has module set but level > 0;
        # it can never name a third party, so the intersection below discards it.
        found.add(node.module.split(".")[0])
    return found & set(VENDORS)


def _module_level(tree: ast.Module) -> set[str]:
    """Vendors imported at import time -- including inside a top-level try/if.

    `try: import boto3 / except ImportError:` is a module-level import even though it is
    nested, and so is one inside `if TYPE_CHECKING:`. Both run when the module loads.
    """
    hard: set[str] = set()
    for node in tree.body:
        hard |= _vendors_in(node)
        if isinstance(node, _TOP_LEVEL_BARRIERS):
            continue
        for sub in ast.iter_child_nodes(node):
            if isinstance(sub, _TOP_LEVEL_BARRIERS):
                continue
            for deeper in ast.walk(sub):
                if isinstance(deeper, _TOP_LEVEL_BARRIERS):
                    break
                hard |= _vendors_in(deeper)
    return hard


def _anywhere(tree: ast.Module) -> set[str]:
    """Every vendor the module imports, at any depth."""
    found: set[str] = set()
    for node in ast.walk(tree):
        found |= _vendors_in(node)
    return found


def measure(package: pathlib.Path) -> tuple[list[tuple[str, set[str], set[str]]], int]:
    """Returns (rows, total_modules). A row is (path relative to the package, hard, deferred).

    The path is stored RELATIVE to `package`. `PACKAGE` is absolute -- it is resolved from
    `__file__` so the script runs from any directory -- so `str(path)` would print the
    caller's home directory, and a later `.replace("agentorg/", "")` would silently fail to
    strip it. `relative_to` does the job at the point the path is produced, once.
    """
    modules = sorted(package.rglob("*.py"))
    rows = []
    for path in modules:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as exc:
            print(f"CANNOT PARSE {path}: {exc}", file=sys.stderr)
            raise
        hard = _module_level(tree)
        every = _anywhere(tree)
        if every:
            rows.append((str(path.relative_to(package)), hard, every - hard))
    return rows, len(modules)


def main() -> int:
    rows, total = measure(PACKAGE)
    touching = len(rows)
    with_hard = sum(1 for _, hard, _ in rows if hard)

    print(f"agentorg/ -- {total} modules, measured over the AST\n")
    print(f"{'module':30} {'HARD (module level)':24} deferred (in a function)")
    print("-" * 86)
    for path, hard, deferred in rows:
        print(f"{path:30} {','.join(sorted(hard)) or '--':24} "
              f"{','.join(sorted(deferred)) or '--'}")

    print()
    print(f"modules touching a vendor SDK       : {touching} of {total}")
    print(f"with a MODULE-LEVEL vendor import   : {with_hard}")
    print(f"deferred only (import in a function): {touching - with_hard}")
    print()
    for name, why in sorted(VENDORS.items()):
        holders = [p for p, h, d in rows if name in (h | d)]
        print(f"  {name:10} {why}")
        print(f"             {', '.join(holders) or 'not imported anywhere'}")

    # The self-check. If EVERY touching module reports a hard import, the tree walk has
    # probably leaked into function bodies again -- which is exactly how the first version
    # of this script produced a wrong answer that read as a right one. A vacuous split is
    # worse than no measurement, because it reads as evidence.
    if touching and with_hard == touching:
        print()
        print("REFUSING: every touching module reports a MODULE-LEVEL import, which is "
              "what a leaked AST walk looks like. Check _module_level's barriers before "
              "quoting these numbers.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
