from unittest.mock import Mock

import boto3
import pytest
from botocore.exceptions import ReadTimeoutError
from botocore.stub import Stubber

from insights.config import Settings
from insights.narrative.llm import BedrockClient, LLMUnavailable
from insights.narrative.prompt import SYSTEM_PROMPT, TOOL_SPEC


def client(monkeypatch):
    sdk = boto3.client(
        "bedrock-runtime",
        region_name="us-west-2",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )
    factory = Mock(return_value=sdk)
    monkeypatch.setattr("insights.narrative.llm.boto3.client", factory)
    result = BedrockClient(Settings())
    config = factory.call_args.kwargs["config"]
    assert config.read_timeout == 60 and config.connect_timeout == 5
    assert config.retries == {"max_attempts": 2, "mode": "standard"}
    return result, sdk


async def test_converse_forces_tool_and_parses_usage(monkeypatch):
    llm, sdk = client(monkeypatch)
    messages = [{"role": "user", "content": [{"text": "Evidence"}]}]
    output = {"narrative": "Test [E1].", "hypotheses": []}
    message = {
        "role": "assistant",
        "content": [
            {
                "toolUse": {
                    "toolUseId": "tool-1",
                    "name": "submit_narrative",
                    "input": output,
                }
            }
        ],
    }
    with Stubber(sdk) as stub:
        stub.add_response(
            "converse",
            {
                "output": {"message": message},
                "stopReason": "tool_use",
                "usage": {"inputTokens": 123, "outputTokens": 45, "totalTokens": 168},
                "metrics": {"latencyMs": 12},
            },
            {
                "modelId": llm.model_id,
                "system": [{"text": SYSTEM_PROMPT}],
                "messages": messages,
                "inferenceConfig": {"maxTokens": 1500, "temperature": 0.2},
                "toolConfig": {
                    "tools": [{"toolSpec": TOOL_SPEC}],
                    "toolChoice": {"tool": {"name": "submit_narrative"}},
                },
            },
        )
        reply = await llm.submit(system=SYSTEM_PROMPT, messages=messages, tool_spec=TOOL_SPEC)
    assert reply.tool_input == output and reply.assistant_message == message
    assert reply.input_tokens == 123 and reply.output_tokens == 45
    assert reply.tool_use_id == "tool-1"
    sdk.close()


async def test_sdk_errors_only_expose_codes(monkeypatch):
    llm, sdk = client(monkeypatch)
    with Stubber(sdk) as stub:
        stub.add_client_error(
            "converse",
            service_error_code="ThrottlingException",
            service_message="private upstream message",
            http_status_code=429,
        )
        with pytest.raises(LLMUnavailable, match=r"^ThrottlingException$") as caught:
            await llm.submit(system=SYSTEM_PROMPT, messages=[], tool_spec=TOOL_SPEC)
        assert "private" not in str(caught.value)
    monkeypatch.setattr(sdk, "converse", Mock(side_effect=ReadTimeoutError(endpoint_url="private")))
    with pytest.raises(LLMUnavailable, match=r"^ReadTimeoutError$"):
        await llm.submit(system=SYSTEM_PROMPT, messages=[], tool_spec=TOOL_SPEC)
    sdk.close()


async def test_ping_requests_one_token_and_reports_only_the_error_code(monkeypatch):
    llm, sdk = client(monkeypatch)
    expected = {
        "modelId": llm.model_id,
        "messages": [{"role": "user", "content": [{"text": "ping"}]}],
        "inferenceConfig": {"maxTokens": 1},
    }
    with Stubber(sdk) as stub:
        stub.add_response(
            "converse",
            {
                "output": {"message": {"role": "assistant", "content": [{"text": "p"}]}},
                "stopReason": "max_tokens",
                "usage": {"inputTokens": 8, "outputTokens": 1, "totalTokens": 9},
                "metrics": {"latencyMs": 5},
            },
            expected,
        )
        stub.add_client_error(
            "converse",
            "UnrecognizedClientException",
            "secret detail",
            403,
            expected_params=expected,
        )
        await llm.ping()
        with pytest.raises(LLMUnavailable) as caught:
            await llm.ping()
    assert str(caught.value) == "UnrecognizedClientException"
