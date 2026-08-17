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
