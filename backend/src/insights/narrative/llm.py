"""LLM client boundary for the narrative: the LLMClient protocol and its Bedrock implementation.

The model only receives the system prompt and messages built in prompt.py.
"""

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

if TYPE_CHECKING:
    from mypy_boto3_bedrock_runtime.type_defs import MessageTypeDef, ToolSpecificationTypeDef

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from insights.config import Settings


@dataclass(frozen=True, slots=True)
class LLMReply:
    tool_input: dict[str, Any] | None
    tool_use_id: str | None
    assistant_message: dict[str, Any]
    input_tokens: int
    output_tokens: int


@dataclass(slots=True)
class LLMUsage:
    attempts: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    first: LLMReply | None = None

    def record(self, reply: LLMReply) -> None:
        """Accumulate returned token usage and retain the reply when attempts is one."""
        if self.attempts == 1:
            self.first = reply
        self.input_tokens += reply.input_tokens
        self.output_tokens += reply.output_tokens


class LLMClient(Protocol):
    """Makes one forced submit_narrative tool call per submit().

    tool_input is None when the reply contains no such tool call. Service or transport
    failures raise LLMUnavailable, which the caller turns into the template fallback.
    """

    model_id: str

    async def submit(
        self, *, system: str, messages: list[dict[str, Any]], tool_spec: dict[str, Any]
    ) -> LLMReply:
        """Submit one tool-constrained request; return its reply or raise LLMUnavailable."""
        ...


class LLMUnavailable(Exception):  # noqa: N818 - public name required by the plan
    def __init__(self, reason: str) -> None:
        """Carry a sanitized transport/service failure reason into narrative fallback handling."""
        self.reason = reason
        super().__init__(reason)


class BedrockClient:
    def __init__(self, settings: Settings) -> None:
        """Create the configured regional Bedrock client with bounded network waits and retries."""
        self.model_id = settings.bedrock_model_id
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=settings.aws_region,
            config=Config(
                read_timeout=settings.llm_timeout_seconds,
                connect_timeout=5,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

    async def submit(
        self, *, system: str, messages: list[dict[str, Any]], tool_spec: dict[str, Any]
    ) -> LLMReply:
        """Run the blocking SDK off the event loop and extract the requested tool output.

        Forced tool use does not establish numeric or causal validity; the local validator
        checks those claims. Cancelling this coroutine need not stop the SDK thread, so SDK
        timeouts still bound its network waits. Only error codes/types cross this boundary.
        """
        try:
            response = await asyncio.to_thread(
                self.client.converse,
                modelId=self.model_id,
                system=[{"text": system}],
                messages=cast("list[MessageTypeDef]", messages),
                inferenceConfig={"maxTokens": 1500, "temperature": 0.2},
                toolConfig={
                    "tools": [{"toolSpec": cast("ToolSpecificationTypeDef", tool_spec)}],
                    "toolChoice": {"tool": {"name": "submit_narrative"}},
                },
            )
        except ClientError as exc:
            raise LLMUnavailable(exc.response["Error"]["Code"]) from None
        except BotoCoreError as exc:
            raise LLMUnavailable(type(exc).__name__) from None
        message: dict[str, Any] = dict(response["output"]["message"])
        tool: dict[str, Any] = next(
            (
                block["toolUse"]
                for block in message["content"]
                if block.get("toolUse", {}).get("name") == "submit_narrative"
            ),
            {},
        )
        return LLMReply(
            tool.get("input"),
            tool.get("toolUseId"),
            message,
            response["usage"]["inputTokens"],
            response["usage"]["outputTokens"],
        )
