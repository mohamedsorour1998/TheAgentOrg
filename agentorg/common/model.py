"""Shared LLM model provider for all agents.

Copied from astrolabe/agents/common/model.py. Uses Bedrock directly via IAM
role by default; falls back to an OpenAI-compatible endpoint only if
LLM_BASE_URL is explicitly set.
"""

from . import config


def create_model(**overrides):
    """Create an LLM model for Strands agents.

    Default: Bedrock with Nova Lite (no external dependency).
    Fallback: OpenAI-compatible endpoint if LLM_BASE_URL is explicitly set.
    """
    if config.LLM_BASE_URL:
        from strands.models.openai import OpenAIModel

        if not config.LLM_API_KEY or config.LLM_API_KEY == "not-needed":
            raise ValueError(
                "LLM_API_KEY must be set when LLM_BASE_URL is configured "
                "(OpenAI-compatible/LiteLLM path)."
            )
        return OpenAIModel(
            model_id=overrides.pop("model_id", config.LLM_MODEL),
            client_args={
                "base_url": config.LLM_BASE_URL,
                "api_key": config.LLM_API_KEY,
            },
            **overrides,
        )

    from strands.models.bedrock import BedrockModel

    return BedrockModel(
        model_id=overrides.pop("model_id", config.BEDROCK_MODEL),
        region_name=config.AWS_REGION,
        **overrides,
    )
