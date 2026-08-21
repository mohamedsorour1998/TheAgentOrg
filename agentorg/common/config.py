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

# OFFLINE=true makes github_ops use plain local git instead of the GitHub API.
#
# IT DOES NOT TAKE THE MODEL OFFLINE, and the comment that used to sit here
# claimed it did -- "so the whole demo runs with the network off". `available()`
# in llm.py reads LLM_DISABLED, LLM_BASE_URL and boto3 credentials. It never
# reads OFFLINE, so on a machine with AWS credentials every agent still calls
# Bedrock. Measured:
#
#   OFFLINE=true python -c "from agentorg.common import llm; print(llm.available())"
#   True
#
# For a genuinely offline run set BOTH:  OFFLINE=true LLM_DISABLED=true
#
# One knob closes the GitHub seam, the other closes the model seam. They are
# separate on purpose -- an offline git demo against a live model is a real
# configuration -- but the word "offline" reads as though it covers both, which
# is why this note is longer than the line it explains. Several plan documents
# still carry the one-knob form; docs/plan/reem/demo_script.md is correct
# because it sets LLM_DISABLED=true throughout.
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

# Remote execution (Sorour) -------------------------------------------------
# REMOTE_AGENTS routes every agent call to its deployed AgentCore runtime
# instead of calling the in-process function. The one reader is
# common/agent_client.call_agent, which graph.py's five call sites go through.
#
# Default false, and that default is load-bearing twice over. It is what keeps
# the LOCAL path the tested one -- the whole suite runs through call_agent, and a
# true default would point the suite at a network call it has no credentials for
# and no business making. It is also the demo's fallback: if the runtimes
# misbehave on Tuesday, unsetting one variable puts the pipeline back on the path
# that has been green all week.
#
# Parsed `== "true"` case-insensitively to match OFFLINE, LLM_DISABLED and
# SCANNERS_REQUIRED above. Plain bool(os.environ.get(...)) would read the string
# "false" as True, which here means every agent call leaving the machine because
# somebody wrote the word "false" down.
REMOTE_AGENTS = os.environ.get("REMOTE_AGENTS", "false").lower() == "true"

# Run state storage (Task 6) ------------------------------------------------
# STATE_BACKEND chooses WHERE a run's decision log and paused state live. The
# readers are agentorg/log.py (append/read), agentorg/gates.py (save/pause/
# resume) and agentorg/approve_server.py (the listing).
#
# Default `local`, and that default is load-bearing for the same two reasons
# REMOTE_AGENTS' is. It keeps the LOCAL path the tested one -- the whole suite
# writes JSONL and state files under runs/ -- and it is what keeps the judged
# demo on the path that has been green all week. The timeline a judge reads is
# the PR and the terminal, not a DynamoDB table, so this knob buys durability
# for a deployed run and must never be needed for a demo.
#
# UNKNOWN VALUES RAISE, they do not fall back to `local`. A typo'd
# STATE_BACKEND=dynamo silently writing to disk is the worst available outcome:
# an operator who believes a run is durable and finds it in a container's
# ephemeral filesystem. That is the same fail-open shape SCANNERS_REQUIRED's
# parsing note above describes, and MAX_REVISION_LOOPS already sets the
# precedent that a malformed knob fails at import rather than being guessed.
#
# Lowercased before comparison, matching OFFLINE / LLM_DISABLED /
# SCANNERS_REQUIRED / REMOTE_AGENTS. Compared against a NAMED tuple rather than
# two string literals so log.py, gates.py, approve_server.py and the tests all
# read the same definition of "the legal backends".
STATE_BACKEND_LOCAL = "local"
STATE_BACKEND_DYNAMODB = "dynamodb"
STATE_BACKENDS = (STATE_BACKEND_LOCAL, STATE_BACKEND_DYNAMODB)

STATE_BACKEND = os.environ.get("STATE_BACKEND", STATE_BACKEND_LOCAL).lower()
if STATE_BACKEND not in STATE_BACKENDS:
    raise ValueError(
        f"STATE_BACKEND={STATE_BACKEND!r} is not a storage backend; expected one "
        f"of {', '.join(STATE_BACKENDS)}. Refused rather than defaulted to "
        f"{STATE_BACKEND_LOCAL!r}: a typo that quietly wrote run state to local "
        f"disk would leave an operator believing a run is durable when it is not."
    )

# The DynamoDB table holding every run's events and its current state document.
# One table, one partition per run: PK `run_id`, SK `ts#event_id` for an event
# row and one reserved sort key for the state document (see log.py). Created by
# infra/Terraform/modules/state/, whose IAM grant is exactly PutItem, Query,
# GetItem and UpdateItem on this table and nothing else.
STATE_TABLE = os.environ.get("STATE_TABLE", "theagentorg-runs")
