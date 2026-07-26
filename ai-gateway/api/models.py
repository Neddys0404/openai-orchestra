"""Pydantic request models for the OpenAI‑compatible endpoints.

These models provide strict validation and type hints for the gateway’s
public API.  They are intentionally lightweight – only the fields that are
currently used by the implementation are included.  Additional fields can be
added later without breaking existing clients.
"""

from __future__ import annotations

from typing import List, Optional, Literal, Any

from pydantic import BaseModel, Field, field_validator, ConfigDict


class TextContentPart(BaseModel):
    type: Literal["text"]
    text: str


class ImageUrl(BaseModel):
    url: str


class ImageContentPart(BaseModel):
    type: Literal["image_url"]
    image_url: ImageUrl


MessageContent = str | list[TextContentPart | ImageContentPart]


class Message(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str = Field(..., description="The role of the message sender.")
    content: MessageContent = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: Optional[str] = Field(None, description="Explicit model name or 'auto' for classifier routing.")
    messages: List[Message] = Field(..., min_items=1)
    tools: list[Any] | None = None
    tool_choice: str | dict[str, Any] | None = None
    stream_options: dict[str, Any] | None = None
    parallel_tool_calls: bool | None = None
    response_format: dict[str, Any] | None = None
    stream: bool = False
    n: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    presence_penalty: float | None = None
    frequency_penalty: float | None = None

    @field_validator("n")
    @classmethod
    def _positive_n(cls, v: int | None):
        if v is not None and v <= 0:
            raise ValueError("n must be positive")
        return v


class CompletionRequest(BaseModel):
    model: str = Field(..., description="Explicit model name.")
    prompt: str = Field(..., min_length=1)
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    n: int = 1
    stream: bool = False

    @field_validator("n")
    @classmethod
    def _positive_n(cls, v: int | None):
        if v is not None and v <= 0:
            raise ValueError("n must be positive")
        return v


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    n: int = 1
    size: str = "1024x1024"
    response_format: str = "b64_json"

    @field_validator("n")
    @classmethod
    def _image_n(cls, v: int):
        if v != 1:
            raise ValueError("Only n=1 is supported for image generation.")
        return v