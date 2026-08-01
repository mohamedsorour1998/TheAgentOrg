"""Bedrock connectivity smoke test.

Confirms create_model() reaches a real Bedrock endpoint and gets a real
completion back — not a stub, not an exception. Run manually:

    python scripts/bedrock_smoke_test.py

Requires: AWS credentials configured (aws configure / env vars) with
bedrock:InvokeModel on the model in agentorg.common.config.BEDROCK_MODEL,
in agentorg.common.config.AWS_REGION.
"""

import sys

from agentorg.common.model import create_model


def main() -> int:
    from strands import Agent

    agent = Agent(model=create_model(), system_prompt="You are terse.")
    reply = agent("say hi")
    print(f"Bedrock reply: {reply}")

    if not reply or not str(reply).strip():
        print("FAIL: empty reply from Bedrock", file=sys.stderr)
        return 1

    print("OK: Bedrock is reachable.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
