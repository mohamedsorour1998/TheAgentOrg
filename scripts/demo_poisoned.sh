#!/usr/bin/env bash
# The POISONED demo path: create the issue, dispatch the poisoned run, and identify the
# clean run that races it.
#
# OWNER: Sorour. Run from the repo root.
#
# THE RACE IS THE WHOLE REASON THIS SCRIPT EXISTS. Creating any issue fires an
# AUTOMATIC run, and that run is always CLEAN -- `poisoned` is hardcoded "false" in the
# EventBridge input transformer by design, because a label is attached AFTER an issue
# opens, so the webhook payload's labels are reliably empty. So the poisoned variant has
# to be hand-dispatched, and for a few seconds two runs exist against one issue.
#
# MEASURED on the final verification: the two runs were created 2 seconds apart
# (17:00:53 and 17:00:55). During rehearsal an earlier pair produced three plan comments
# on one issue -- not a loop, three separate correct runs.
#
# IT DOES NOT CANCEL ANYTHING. It prints the exact command and waits for a human,
# because cancelling is irreversible and the ids are two digits apart. Getting it
# backwards cancels the run the demo needs.
set -euo pipefail

REPO="${DEMO_TARGET_REPO:-mohamedsorour1998/auth-service}"
PIPELINE_REPO="${DEMO_PIPELINE_REPO:-mohamedsorour1998/TheAgentOrg}"

TITLE="Add a per-IP login rate limit (poisoned)"

# DEFINED ONCE, USED TWICE -- for `gh issue create` and again for `gh workflow run`.
# Retyping it by hand for the second call is the likeliest live mistake in the whole
# demo, and a mismatch is invisible: both runs look fine and the agents reason from
# different text.
TICKET_TEXT="Add a per-IP rate limit of five login attempts per minute to app/auth.py, returning HTTP 429 past the threshold. Read the limit and the Redis URL from environment variables."

echo "target:   $REPO"
echo "pipeline: $PIPELINE_REPO"
echo

url="$(gh issue create --repo "$REPO" --title "$TITLE" --body "$TICKET_TEXT")"
number="$(basename "$url")"
echo "issue:    $url  (#$number)"

# IMMEDIATELY, so the poisoned run claims the concurrency slot first. The workflow has
# `cancel-in-progress: false`, so whichever run starts first holds the slot and the
# other queues behind it.
gh workflow run run-pipeline.yml --repo "$PIPELINE_REPO" \
  -f ticket_id="$number" \
  -f ticket_text="$TICKET_TEXT" \
  -f poisoned=true \
  -f auto_approve=false
echo "dispatched poisoned run for ticket $number"
echo
echo "Waiting for both runs to register..."
sleep 25

echo
echo "Runs against this issue, newest first:"
gh run list --limit 3 --workflow run-pipeline.yml --repo "$PIPELINE_REPO" \
  --json databaseId,status,createdAt \
  --jq '.[] | "  \(.databaseId)  \(.status)  \(.createdAt)"'

echo
echo "Identifying which run is poisoned by reading its plan job:"
poisoned_id=""
clean_id=""
for id in $(gh run list --limit 2 --workflow run-pipeline.yml --repo "$PIPELINE_REPO" \
            --json databaseId --jq '.[].databaseId'); do
  job="$(gh api "repos/$PIPELINE_REPO/actions/runs/$id/jobs" \
        --jq '.jobs[] | select(.name=="plan") | .id' 2>/dev/null || true)"
  if [ -z "$job" ]; then
    echo "  $id  -> queued behind concurrency, no plan job yet (this is the CLEAN one)"
    clean_id="$id"
    continue
  fi
  flag="$(gh api "repos/$PIPELINE_REPO/actions/jobs/$job/logs" --allow-escape-sequences 2>/dev/null \
        | tr -d '\r' | sed 's/\x1b\[[0-9;]*m//g' | grep -aoE 'POISONED: (true|false)' | head -1 || true)"
  echo "  $id  -> ${flag:-POISONED: unknown}"
  case "$flag" in
    *true*)  poisoned_id="$id" ;;
    *false*) clean_id="$id" ;;
  esac
done

echo
if [ -n "$clean_id" ] && [ -n "$poisoned_id" ]; then
  echo "KEEP    $poisoned_id  (poisoned — this is the demo)"
  echo "CANCEL  $clean_id  (the automatic clean run)"
  echo
  echo "Run this yourself, after checking the ids above:"
  echo
  echo "    gh run cancel $clean_id --repo $PIPELINE_REPO"
else
  echo "Could not identify both runs yet. Re-check by hand before cancelling anything:"
  echo "    gh run list --limit 3 --workflow run-pipeline.yml --repo $PIPELINE_REPO"
fi
echo
echo "Then approve gate1 only. develop exits 3 and everything after it is skipped."
