from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base for LLM-produced or trust-boundary models: unknown fields are rejected."""

    model_config = ConfigDict(extra="forbid")
