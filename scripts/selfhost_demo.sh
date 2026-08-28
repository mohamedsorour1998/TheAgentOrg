#!/usr/bin/env bash
# The self-hosted demo, as one command. Lane F, task F5.
#
#     scripts/selfhost_demo.sh
#
# ONE CLAIM, AND IT IS NARROW: a poisoned ticket is blocked by a pipeline whose
# model runs on this machine, and no AWS hostname is reached while doing it.
#
# WHAT MAKES THAT CLAIM CHECKABLE rather than assertable is `--strict`, which
# aborts the run at the call site of any contact with an AWS host. So this script
# cannot report success for a run that quietly used Bedrock -- which matters,
# because this machine HAS AWS credentials in ~/.aws and the Bedrock path is one
# unset environment variable away.
#
# WHY NOT `docker compose up`. The Compose stack is the deployment vehicle
# (`infra/selfhost/docker-compose.yml`) and it has never been started: there is no
# Docker daemon here. This script runs the same pipeline against a host-native
# model, so the DEMO is real even though the containerised packaging of it is not
# yet proven. Presenting the compose file as the demonstrated thing would be the
# over-claim; presenting this is not.
#
# THE FALLBACK IF THE MODEL MISBEHAVES on the day: unset LLM_BASE_URL and the
# pipeline returns to Bedrock, which has been green all week. That is deliberate
# and it is the same argument as `REMOTE_AGENTS` defaulting false -- but note it
# is then NO LONGER a self-hosted demo, and the witness will say so out loud
# rather than letting the slide stay on screen.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  # `.venv-main` LIVES IN THE MAIN CHECKOUT AND IN NO WORKTREE. Measured: running
  # this script from `.claude/worktrees/<lane>` died with
  # `.venv-main/bin/python: No such file or directory`, because the venv is
  # gitignored and a linked worktree does not inherit it. That is the same class as
  # the three divergences CLAUDE.md lists under "A GATE VERIFIED ONLY IN THE MAIN
  # CHECKOUT IS NOT VERIFIED" -- state on disk but not in the index.
  #
  # `git rev-parse --git-common-dir` resolves to the MAIN checkout's `.git` from
  # inside a worktree (where `--git-dir` gives the per-worktree directory), so its
  # parent is the shared root. Falls back to the repo root for a plain clone.
  COMMON_GIT_DIR="$(git rev-parse --git-common-dir 2>/dev/null || echo "")"
  if [ -n "$COMMON_GIT_DIR" ] && [ -x "$(dirname "$COMMON_GIT_DIR")/.venv-main/bin/python" ]; then
    PYTHON="$(dirname "$COMMON_GIT_DIR")/.venv-main/bin/python"
  else
    PYTHON=".venv-main/bin/python"
  fi
fi
if [ ! -x "$PYTHON" ]; then
  echo "REFUSING: no interpreter at $PYTHON" >&2
  echo "  set PYTHON=/path/to/python. Note .venv-main is gitignored, so it" >&2
  echo "  exists in the main checkout and in no linked worktree." >&2
  exit 2
fi

MODEL="${SELFHOST_MODEL:-qwen2.5-coder:7b}"
GATEWAY="${LLM_BASE_URL:-http://127.0.0.1:11434/v1}"
OUT="${SELFHOST_OUT:-runs/selfhost-demo.json}"

echo "== the self-hosted demo =="
echo "model:   $MODEL"
echo "gateway: $GATEWAY"
echo

# 1. IS THE GATEWAY THERE? Checked before the pipeline, because the failure mode
#    otherwise is silent: `llm.text()` catches a connection error BY DESIGN, every
#    agent serves its fixture, and the run still ends `blocked` with
#    `provenance: scanners` -- a result indistinguishable, on the projector, from
#    the one this demo is claiming. MEASURED: that is exactly what happened on the
#    first attempt here, when the `openai` package was missing.
if ! curl -fsS -m 5 "${GATEWAY%/v1}/api/tags" >/dev/null 2>&1; then
  echo "REFUSING: no model gateway answered at $GATEWAY" >&2
  echo "  start one with:  ollama serve  &&  ollama pull $MODEL" >&2
  echo "  a missing gateway does NOT fail loudly -- every agent would serve its" >&2
  echo "  fixture and the run would still print a correct-looking block." >&2
  exit 2
fi
echo "gateway answered"

# 2. IS THE MODEL PULLED? A gateway with no model answers the tag list and then
#    404s the completion, which lands in the same silent fixture fallback.
if ! curl -fsS -m 5 "${GATEWAY%/v1}/api/tags" | grep -q "$MODEL"; then
  echo "REFUSING: the gateway is up but does not have $MODEL" >&2
  echo "  ollama pull $MODEL" >&2
  exit 2
fi
echo "model present"

# 3. ARE THE SCANNERS HERE? The security verdict is the point of the demo, and a
#    fixture-fallback verdict says `block` too. This does not REFUSE on absence --
#    the pipeline handles it and records `fixture-fallback` honestly -- it warns,
#    so a reader of the output knows which of the two they got before the verdict
#    appears rather than after.
missing=""
for tool in gitleaks trivy semgrep; do
  command -v "$tool" >/dev/null 2>&1 || missing="$missing $tool"
done
if [ -n "$missing" ]; then
  echo "WARNING: absent scanner(s):$missing"
  echo "  the run will record provenance=fixture-fallback rather than scanners."
  echo "  It will still BLOCK -- for a different reason than the demo claims."
else
  echo "scanners present: gitleaks trivy semgrep"
fi
echo

# 4. THE RUN. `--strict` aborts on any AWS contact; `--require-model` refuses a
#    fixture run; `--require-snapshot` refuses a run where the agents cannot see
#    the target repository. All three are pre-flight or in-flight REFUSALS rather
#    than warnings, because a caveated success is what gets quoted without its
#    caveat.
#
#    DEMO_REPO, not GITHUB_REPO -- config.py's one name mismatch. With it unset
#    `repo_snapshot.snapshot()` returns {} and every agent reasons blind.
DEMO_REPO="${DEMO_REPO:-mohamedsorour1998/auth-service}" \
OFFLINE=true \
LLM_BASE_URL="$GATEWAY" \
LLM_API_KEY="${LLM_API_KEY:-selfhost-local-gateway-ignores-this}" \
LLM_MODEL="$MODEL" \
PYTHONPATH="$REPO_ROOT" \
  "$PYTHON" scripts/selfhost_measure.py \
    --label "selfhost $MODEL" \
    --poisoned \
    --strict \
    --require-snapshot \
    --out "$OUT"

echo
echo "== what to read off that =="
echo "  verdict: block          the deterministic rule refused the change"
echo "  provenance: scanners    real gitleaks, not a fixture"
echo "  finding lines: [3, 4]   real scanners; the fixture says [4, 5]"
echo "  network: no AWS hostname resolved or contacted"
echo
echo "  source: model           the LOCAL model answered. If this reads"
echo "                          'fixture', the block above is this repository's"
echo "                          own fixture and the demo proved nothing."
echo
echo "record: $OUT"
