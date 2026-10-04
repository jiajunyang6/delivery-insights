from copy import deepcopy
from typing import Any

from insights.narrative.llm import LLMReply


class FakeLLMClient:
    def __init__(
        self, script: list[dict[str, Any] | Exception], model_id: str = "fake-model"
    ) -> None:
        self.model_id = model_id
        self.script = iter(script)
        self.ping_error: Exception | None = None
        self.calls: list[dict[str, Any]] = []

    async def submit(
        self, *, system: str, messages: list[dict[str, Any]], tool_spec: dict[str, Any]
    ) -> LLMReply:
        self.calls.append(
            deepcopy({"system": system, "messages": messages, "tool_spec": tool_spec})
        )
        item = next(self.script)
        if isinstance(item, Exception):
            raise item
        identifier = f"fake-{len(self.calls)}"
        message = {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": identifier,
                        "name": "submit_narrative",
                        "input": item,
                    }
                }
            ],
        }
        return LLMReply(deepcopy(item), identifier, message, 0, 0)

    async def ping(self) -> None:
        if self.ping_error:
            raise self.ping_error
