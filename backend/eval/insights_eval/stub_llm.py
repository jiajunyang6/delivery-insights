from typing import Any

import orjson

from insights.narrative.llm import LLMReply
from insights.narrative.template import build_template


class StubLLMClient:
    model_id = "stub"

    async def submit(
        self, *, system: str, messages: list[dict[str, Any]], tool_spec: dict[str, Any]
    ) -> LLMReply:
        text = messages[0]["content"][0]["text"]
        pack = orjson.loads(text.split("Evidence pack (JSON):\n", 1)[1])
        evidence = {e["id"]: e for e in pack["evidence"]}
        # This is the same code-owned revert-rate guardrail, using only the supplied pack.
        revert = evidence.get("E10", {})
        warning = revert.get("change_pp") is not None and revert["change_pp"] >= 1
        output = build_template(pack, {"guardrail": {"verdict": "watch" if warning else "ok"}})
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
