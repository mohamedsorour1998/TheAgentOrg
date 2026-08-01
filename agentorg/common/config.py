"""Shared configuration for The Agent Org.

Mirrors astrolabe/agents/common/config.py. All AWS + LLM knobs live here so no
agent hardcodes a model id or region.
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
