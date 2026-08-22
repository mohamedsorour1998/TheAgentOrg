#!/usr/bin/env bash
# The CLEAN demo path: create the issue and let the pipeline start itself.
#
# OWNER: Sorour. Run from the repo root during the demo, or just read the issue body
# out of here and create it in the browser -- the auto-trigger fires either way, and
# creating it in the GitHub UI is the more convincing thing for a judge to watch.
#
# WHY THIS SCRIPT EXISTS AT ALL. The ticket text is load-bearing. Measured on run
# 32557597915: with a vaguer ticket the reviewer asked for email-based rate limiting,
# the developer produced IP-based, and the run correctly ended `failed` at the revision
# cap with the scanners reporting PASS. Nobody approved it. That is the pipeline working
# and the demo lost, so the wording lives in version control rather than in someone's
# memory at 3pm.
#
# It does NOT dispatch anything. Opening the issue is the whole trigger, and that is
# the point being demonstrated -- nobody typed a command.
set -euo pipefail

REPO="${DEMO_TARGET_REPO:-mohamedsorour1998/auth-service}"

TITLE="Add a per-IP rate limit of five login attempts per minute to app/auth.py"

# One variable, used once here. The poisoned script reuses its own copy for TWO calls,
# which is where retyping actually bites -- see demo_poisoned.sh.
read -r -d '' BODY <<'TICKET' || true
Return HTTP 429 once a client exceeds five failed login attempts in a rolling
sixty-second window. Read the attempt limit and the Redis URL from environment
variables so they are configurable without a code change. Keep the existing
successful-login behaviour unchanged.
TICKET

echo "repo:  $REPO"
echo "title: $TITLE"
echo
echo "Creating the issue. The pipeline should start on its own within ~6 seconds."
echo

url="$(gh issue create --repo "$REPO" --title "$TITLE" --body "$BODY")"
echo "issue:  $url"
echo
echo "Now watch Actions. Nobody typed a command to start it:"
echo "  gh run list --limit 1 --workflow run-pipeline.yml"
echo
echo "Then approve gate1, gate2 and gate3 in the browser as each one pauses."
