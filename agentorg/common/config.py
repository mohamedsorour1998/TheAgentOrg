"""Shared configuration for The Agent Org.

All AWS + LLM knobs live here so no agent hardcodes a model id or region.
"""

import os

# LLM configuration --------------------------------------------------------
# Default path: Bedrock via IAM role (no external dependency).
# Fallback path: OpenAI-compatible gateway when LLM_BASE_URL is set.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "not-needed")
LLM_MODEL = os.environ.get("LLM_MODEL", "us.amazon.nova-2-lite-v1:0")
BEDROCK_MODEL = os.environ.get("BEDROCK_MODEL", "us.amazon.nova-2-lite-v1:0")

# AWS ----------------------------------------------------------------------
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

# Pipeline behaviour -------------------------------------------------------
# Block threshold for the deterministic security gate (see state.compute_security_verdict).
SECURITY_BLOCK_THRESHOLD = os.environ.get("SECURITY_BLOCK_THRESHOLD", "high")
# Cap on developer<->reviewer revision loops so a run can't spin forever.
MAX_REVISION_LOOPS = int(os.environ.get("MAX_REVISION_LOOPS", "3"))

# OFFLINE=true makes github_ops use plain local git instead of the GitHub API,
# so the whole demo runs with the network off.
OFFLINE = os.environ.get("OFFLINE", "false").lower() == "true"

# GitHub seam (Mariam) ------------------------------------------------------
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("DEMO_REPO", "")   # the shared target repo, "owner/name"

# LLM availability (Sorour) -------------------------------------------------
# Set true to force every agent onto its fixture without attempting a model
# call. CI sets this so the suite never needs AWS credentials.
LLM_DISABLED = os.environ.get("LLM_DISABLED", "false").lower() == "true"

# Offline demo workspace (Mariam) -------------------------------------------
# Where the offline path does its real git work, and where blocked-run reasons
# are recorded instead of being posted as PR comments. Both default under runs/,
# which is gitignored, so a demo run never dirties this repository. The suite
# redirects them at a per-test tmp_path -- see tests/conftest.py, seam 2.
OFFLINE_REPO = os.environ.get("OFFLINE_REPO", "runs/offline-demo")
OFFLINE_NOTES = os.environ.get("OFFLINE_NOTES", "runs/offline-demo/NOTES.md")

# Scanner resilience (Habiba) ----------------------------------------------
# SCANNERS_REQUIRED distinguishes a scanner that is ABSENT from one that is
# BROKEN, which are not the same fault and must not get the same answer.
#
# Default false, and that default is load-bearing. With no binaries on PATH --
# which is exactly how CI's `test` job runs, deliberately, so the suite stays a
# fast offline unit run instead of pulling a ~108 MB vulnerability database on
# every push -- each wrapper raises, agents/security.py catches it and falls
# back to the FIXTURE verdict, and the poisoned diff still blocks on its two
# AWS-key findings.
#
# TWO NUMBERS, BOTH MEASURED, because they are different facts and the difference
# is what makes them useful. There are EIGHT literal `assert len(...blocking) ==
# 2` statements in tests/ (AST count -- note one is a CHAINED comparison at
# test_functional_contract.py:135, which a grep for `blocking) == 2` finds but a
# single-operator AST filter misses). Of those, FOUR actually depend on this
# default: test_pipeline_smoke.py:20, test_agent_fallbacks.py:466,
# test_block_determinism.py:13, test_gates_cli.py:383 -- measured by flipping
# this default to true and seeing which go red. The other four reach `blocking`
# without going through the scanner path at all: three patch
# run_all_scanners directly and one reads the fixture file.
#
# Flipping this default to true turns the fixture's two AWS-key findings into
# three scanner-error findings and takes those four red (plus 24 other tests that
# assert on verdicts and logs rather than on this count), so a missing binary
# stays a development affordance rather than a fault.
#
# Set it true on the demo machine and in any production image. There, a scanner
# that is not installed is a real defect: the gate would otherwise report clean
# because it never ran, which is failing OPEN -- the one shape this lane exists
# to prevent. True promotes absent to fault, and the fault blocks loudly instead
# of quietly borrowing a fixture verdict.
#
# Parsed `== "true"` case-insensitively to match OFFLINE and LLM_DISABLED above.
# Plain bool(os.environ.get(...)) would read the string "false" as True, which is
# the worst available outcome: a machine that believes it has fail-closed
# scanners and does not.
SCANNERS_REQUIRED = os.environ.get("SCANNERS_REQUIRED", "false").lower() == "true"

# Wall-clock ceiling for a single scanner invocation, in seconds. A scanner that
# hangs is worse than one that crashes: with no timeout the pipeline waits
# forever at the gate, which on a projector is indistinguishable from a freeze
# and produces no verdict at all. int, not str, because it is handed straight to
# subprocess's timeout=.
#
# 120s is chosen against measured cost, not guessed: the full suite with all
# three real binaries takes ~173s in total, and trivy's first run has to resolve
# a vulnerability database. A ceiling that trips on an honest slow scan would be
# a self-inflicted block, since a timeout is a fault and fails closed.
SCANNER_TIMEOUT_SECONDS = int(os.environ.get("SCANNER_TIMEOUT_SECONDS", "120"))
