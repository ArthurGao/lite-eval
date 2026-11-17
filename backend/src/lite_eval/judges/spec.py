"""A judge's configuration: a suite's `judges: {role: {...}}` maps a role to one of these
(design §9: code names roles, never models). Its `identity()` is the instrument (L3): model,
route, temperature and thinking; the host and the key are how it is reached, not what it
decides."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from lite_eval.contracts import Billing


class Price(BaseModel):
    """US dollars per million tokens, at list price; reasoning tokens are billed as output."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)


class JudgeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["ollama", "openai"]  # the protocol: Ollama /api/chat, or chat completions
    model: str = Field(min_length=1)
    base_url: str = Field(min_length=1)
    channel: str | None = None  # the route recorded (L20: a subscription profile is not the API)
    temperature: float = 0.0
    think: bool | str | None = False  # Ollama `think`; for openai a string is `reasoning_effort`
    api_key_env: str | None = None  # read at call time, masked by value (design §18.12)
    billing: Billing = "unknown"
    price: Price | None = None
    max_tokens: int | None = Field(default=None, gt=0)

    @property
    def route(self) -> str:
        return self.channel or self.provider

    def identity(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "channel": self.route,
            "model": self.model,
            "temperature": self.temperature,
            "think": self.think,
        }
