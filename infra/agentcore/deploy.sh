#!/usr/bin/env bash
#
# Deploy the five agents to Bedrock AgentCore.
#
# ############################################################################
# ## THIS SCRIPT PERFORMS LIVE, BILLABLE AWS ACTIONS.                       ##
# ##                                                                        ##
# ## `agentcore launch` builds an ARM64 image, PUSHES it to ECR, and        ##
# ## CREATES OR UPDATES a real AgentCore runtime in account 339712964409.   ##
# ## It costs money, it is not a dry run, and `--auto-update-on-conflict`   ##
# ## means it will OVERWRITE an existing runtime of the same name.          ##
# ##                                                                        ##
# ## It has never been run. As of the commit that added this file,          ##
# ## ListAgentRuntimes in us-east-1 returned 10 READY runtimes and NONE     ##
# ## was theagentorg_* -- so the first run creates all five from nothing.   ##
# ############################################################################
#
# WHY A CHECKED-IN SCRIPT rather than commands pasted from a doc: the same
# reasoning .github/workflows/ci.yml:202-206 gives for scripts/scan_gate.py --
# "a checked-in script rather than a heredoc so that the bytes CI runs are the
# bytes anyone can run on a laptop". A ten-command sequence retyped under time
# pressure before a judged demo is where a wrong -n or a missing -er goes in.
#
# HOW TO RUN IT (it refuses to run by accident -- three gates, by design):
#
#   1. Read this file.
#   2. export AGENTORG_DEPLOY_I_MEAN_IT=yes
#   3. bash infra/agentcore/deploy.sh
#
# It then prints exactly what it will do and waits for you to type the word
# `deploy`. Nothing has happened yet at that point. Ctrl-C is safe.
#
# To see the commands without running anything:
#   bash infra/agentcore/deploy.sh --dry-run
# --dry-run needs no env var and touches no AWS. It is the safe default: with no
# arguments and no env var, the script prints usage and exits non-zero.
#
# IDENTIFIERS: every value below is read from docs/plan/week1-verification-log.md
# (lines 11-30) and docs/plan/sorour/week3.md:292-293. Nothing is derived from
# Terraform state -- the task text forbids re-deriving them -- and nothing is
# recalled. Two namespaces are in play and both are correct in their own place:
#   * AgentCore RUNTIME names use UNDERSCORES: theagentorg_planner, ...
#     (docs/plan/sorour/week3.md:292 `-n theagentorg_planner`). Corroborated
#     independently: the account's other 10 runtimes all use underscores.
#   * ECR REPOSITORY names use HYPHENS: theagentorg-shared-planner-agent, ...
#     `agentcore launch` derives the repo itself; the names are listed here for
#     the reader, not passed as arguments.
#
# Fail fast and loudly. -u so an unset variable cannot expand to empty and send a
# malformed identifier to AWS; -o pipefail so a failure mid-pipe is not masked.
set -euo pipefail

# --- Recorded identifiers (docs/plan/week1-verification-log.md:11-30) ---------

readonly AWS_ACCOUNT="339712964409"
readonly AWS_REGION_DEPLOY="us-east-1"

# docs/plan/week1-verification-log.md:21
readonly RUNTIME_ROLE_ARN="arn:aws:iam::339712964409:role/theagentorg-shared-agentcore-runtime-role"

# docs/plan/sorour/week3.md:293 passes this to `agentcore launch --env`.
readonly BEDROCK_MODEL_ID="us.amazon.nova-2-lite-v1:0"

# entrypoint file : AgentCore runtime name : ECR repository (for the reader).
# Order is the spec's: planner first, "verify, then repeat for the other four"
# (docs/plan/mariam/week3.md:56-57).
readonly AGENTS=(
  "planner.py:theagentorg_planner:theagentorg-shared-planner-agent"
  "developer.py:theagentorg_developer:theagentorg-shared-developer-agent"
  "reviewer.py:theagentorg_reviewer:theagentorg-shared-reviewer-agent"
  "security.py:theagentorg_security:theagentorg-shared-security-agent"
  "sre.py:theagentorg_sre:theagentorg-shared-sre-agent"
)

# Resolve the agents directory from THIS script's location, so the script works
# from any working directory. `agentcore configure -e planner.py` takes a bare
# filename, so the commands must run from inside agentorg/agents/.
# Resolved with `cd && pwd` rather than left as `${SCRIPT_DIR}/../../...`, so the
# path this script PRINTS in its plan is the path a reader can paste. An
# unresolved `../..` in the confirmation output is exactly the kind of detail
# that gets skimmed past under time pressure.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if [[ ! -d "${SCRIPT_DIR}/../../agentorg/agents" ]]; then
  echo "agents directory not found relative to ${SCRIPT_DIR}" >&2
  exit 1
fi
AGENTS_DIR="$(cd -- "${SCRIPT_DIR}/../../agentorg/agents" && pwd)"
readonly AGENTS_DIR

usage() {
  cat <<'USAGE'
Deploy the five agents to Bedrock AgentCore. LIVE, BILLABLE.

  bash infra/agentcore/deploy.sh --dry-run    print the commands, touch nothing
  AGENTORG_DEPLOY_I_MEAN_IT=yes \
    bash infra/agentcore/deploy.sh            deploy, after typing 'deploy'

Refuses to run without AGENTORG_DEPLOY_I_MEAN_IT=yes. Read the file first.
USAGE
}

# Print the exact commands for one agent. Used by --dry-run AND by the live path,
# so what you are shown is what runs -- not a second description that can drift.
emit_commands() {
  local entrypoint="$1" runtime_name="$2"
  echo "  agentcore configure -e ${entrypoint} -n ${runtime_name} \\"
  echo "      -er ${RUNTIME_ROLE_ARN} \\"
  echo "      -rf requirements.txt -r ${AWS_REGION_DEPLOY} -ni"
  echo "  agentcore launch --auto-update-on-conflict \\"
  echo "      --env BEDROCK_MODEL=${BEDROCK_MODEL_ID}"
}

show_plan() {
  echo "AgentCore deploy plan"
  echo "  account : ${AWS_ACCOUNT}"
  echo "  region  : ${AWS_REGION_DEPLOY}"
  echo "  role    : ${RUNTIME_ROLE_ARN}"
  echo "  model   : ${BEDROCK_MODEL_ID}"
  echo "  cwd     : ${AGENTS_DIR}"
  echo
  local entry
  for entry in "${AGENTS[@]}"; do
    IFS=':' read -r entrypoint runtime_name ecr_repo <<<"${entry}"
    echo "--- ${runtime_name}  (image -> ${ecr_repo}) ---"
    emit_commands "${entrypoint}" "${runtime_name}"
    echo
  done
}

# --- Gate 1: an explicit mode argument, or the env var -----------------------

DRY_RUN=false
case "${1:-}" in
  --dry-run) DRY_RUN=true ;;
  -h|--help) usage; exit 0 ;;
  "")        ;;
  *)         echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
esac

if [[ "${DRY_RUN}" == true ]]; then
  echo "DRY RUN -- nothing below is executed, no AWS call is made."
  echo
  show_plan
  exit 0
fi

# --- Gate 2: the env var must be set to exactly `yes` ------------------------

if [[ "${AGENTORG_DEPLOY_I_MEAN_IT:-}" != "yes" ]]; then
  echo "REFUSING TO RUN: this script performs live, billable AWS actions." >&2
  echo >&2
  echo "It builds five ARM64 images, pushes them to ECR in account ${AWS_ACCOUNT}," >&2
  echo "and creates or OVERWRITES five AgentCore runtimes in ${AWS_REGION_DEPLOY}." >&2
  echo >&2
  echo "To see the commands without running them:" >&2
  echo "  bash infra/agentcore/deploy.sh --dry-run" >&2
  echo >&2
  echo "To actually deploy, read this file, then:" >&2
  echo "  export AGENTORG_DEPLOY_I_MEAN_IT=yes" >&2
  exit 1
fi

# --- Gate 3: the required tooling must exist, checked before anything runs ---
#
# Checked up front rather than per-agent: discovering a missing CLI after the
# planner deployed leaves a half-deployed set, which is the worst state to
# debug under time pressure.

if ! command -v agentcore >/dev/null 2>&1; then
  echo "agentcore CLI not found on PATH." >&2
  echo "Install it (docs/plan/mariam/week3.md:50):" >&2
  echo "  pip install bedrock-agentcore-starter-toolkit" >&2
  exit 1
fi

if [[ ! -d "${AGENTS_DIR}" ]]; then
  echo "agents directory not found: ${AGENTS_DIR}" >&2
  exit 1
fi

if [[ ! -f "${AGENTS_DIR}/requirements.txt" ]]; then
  echo "requirements.txt not found beside the agents: ${AGENTS_DIR}/requirements.txt" >&2
  echo "AgentCore builds from it (-rf requirements.txt); it is not optional." >&2
  exit 1
fi

# --- Gate 4: interactive confirmation, after showing the real plan -----------

show_plan
echo "This CREATES OR OVERWRITES the five runtimes above. It costs money."
echo "Type 'deploy' to proceed, anything else to abort."
read -r -p "> " confirmation
if [[ "${confirmation}" != "deploy" ]]; then
  echo "Aborted. Nothing was deployed."
  exit 1
fi

# --- Live deploy -------------------------------------------------------------

cd "${AGENTS_DIR}"

for entry in "${AGENTS[@]}"; do
  IFS=':' read -r entrypoint runtime_name ecr_repo <<<"${entry}"

  echo
  echo "=== ${runtime_name} (${entrypoint}) -> ${ecr_repo} ==="

  # -ni = non-interactive. -er takes the runtime role ARN, quoted because an
  # ARN contains colons. No `|| true` anywhere: set -e must stop the run on the
  # first failure rather than press on and leave a partial set that later reads
  # as a working deploy.
  agentcore configure \
    -e "${entrypoint}" \
    -n "${runtime_name}" \
    -er "${RUNTIME_ROLE_ARN}" \
    -rf requirements.txt \
    -r "${AWS_REGION_DEPLOY}" \
    -ni

  agentcore launch \
    --auto-update-on-conflict \
    --env "BEDROCK_MODEL=${BEDROCK_MODEL_ID}"

  echo "--- status: ${runtime_name} ---"
  agentcore status
done

echo
echo "All five configure/launch pairs completed."
echo
echo "VERIFY BEFORE BELIEVING IT. Two independent checks:"
echo
echo "  1. The CLI's own view, per the spec's done-condition"
echo "     (docs/plan/sorour/week3.md:305-307):"
echo "       cd ${AGENTS_DIR}"
echo "       agentcore status                      # each runtime READY"
echo "       agentcore invoke '{\"task\":\"say hi\"}'  # a real completion, not an auth error"
echo
echo "  2. This repository's own read-only check, which reads"
echo "     ListAgentRuntimes rather than the CLI's local state:"
echo "       python -c 'from agentorg.github_ops import deploy_note; print(deploy_note())'"
echo "     Before this deploy it reported 0 of 5 runtimes ready. It has never yet"
echo "     run against a real deployed runtime, so this is also the cheapest"
echo "     end-to-end confirmation that that code path works."
echo
echo "If invoke returns AccessDenied on Bedrock or on the ECR pull, the fix is the"
echo "runtime role's policy, not the CLI (docs/plan/sorour/week3.md:302-304)."
