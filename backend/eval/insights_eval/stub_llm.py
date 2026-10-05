"""Offline LLM-protocol stub using template wording; it does not simulate model reasoning."""

from typing import Any

import orjson

from insights.narrative.llm import LLMReply
from insights.narrative.template import build_template


class StubLLMClient:
    """Provide deterministic tool replies for exercising the narrative and evaluation pipeline."""

    model_id = "stub"

    async def ping(self) -> None:
        """No model to reach; the stub is always available."""

    async def submit(
        self, *, system: str, messages: list[dict[str, Any]], tool_spec: dict[str, Any]
    ) -> LLMReply:
        """Extract the initial evidence pack and return template wording as a forced-tool reply.

        The stub depends on the prompt's single-line JSON format and ignores repair feedback,
        system text and tool schema. Zero token counts reflect no model call; production
        generation labels this protocol reply as LLM output, not as its fallback template.
        """
        text = messages[0]["content"][0]["text"]
        pack_json = text.split("Evidence pack (JSON):\n", 1)[1].split("\n", 1)[0]
        output = build_template(orjson.loads(pack_json))
        message = {
            "role": "assistant",
            "content": [
                {
                    "toolUse": {
                        "toolUseId": "stub-submit",
                        "name": "submit_narrative",
                        "input": output,
                    }
                }
            ],
        }
        return LLMReply(output, "stub-submit", message, 0, 0)
