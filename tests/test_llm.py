from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError

from app.llm import (
    adapt_responses_output,
    create_completion,
    needs_responses_fallback,
    to_responses_input,
    to_responses_tools,
)


def _bad_request(message: str) -> BadRequestError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(400, request=request)
    return BadRequestError(message, response=response, body=None)


def test_needs_responses_fallback_detects_reasoning_models() -> None:
    assert needs_responses_fallback(_bad_request(
        "Function tools with reasoning_effort are not supported for gpt-5.6-terra … use /v1/responses"
    )) is True
    assert needs_responses_fallback(_bad_request("context length exceeded")) is False


def test_to_responses_tools_flattens_spec() -> None:
    tools = [{"type": "function", "function": {"name": "remind_add", "description": "d", "parameters": {"type": "object"}}}]
    assert to_responses_tools(tools) == [{"type": "function", "name": "remind_add", "description": "d", "parameters": {"type": "object"}}]


def test_to_responses_input_maps_tool_calls_and_outputs() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "todo_add", "arguments": '{"title":"x"}'}}
        ]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"ok":true}'},
    ]
    items = to_responses_input(messages)
    assert items[0] == {"role": "system", "content": "sys"}
    assert items[1] == {"role": "user", "content": "hi"}
    assert items[2] == {"type": "function_call", "call_id": "c1", "name": "todo_add", "arguments": '{"title":"x"}'}
    assert items[3] == {"type": "function_call_output", "call_id": "c1", "output": '{"ok":true}'}


def test_adapt_responses_output_builds_chat_shape() -> None:
    response = SimpleNamespace(
        output=[
            SimpleNamespace(type="function_call", call_id="c9", name="remind_add", arguments='{"text":"y"}'),
            SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text="done")]),
        ],
        output_text="done",
    )
    adapted = adapt_responses_output(response)
    message = adapted.choices[0].message
    assert message.content == "done"
    assert message.tool_calls[0].id == "c9"
    assert message.tool_calls[0].function.name == "remind_add"
    assert message.tool_calls[0].function.arguments == '{"text":"y"}'


class FakeChat:
    def __init__(self, error: Exception | None, result=None):
        self._error = error
        self._result = result
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        if self._error:
            raise self._error
        return self._result


class FakeResponses:
    def __init__(self, result):
        self._result = result
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        self.captured = kwargs
        return self._result


class FakeClient:
    def __init__(self, chat_error=None, chat_result=None, responses_result=None):
        self.chat = SimpleNamespace(completions=FakeChat(chat_error, chat_result))
        self.responses = FakeResponses(responses_result)


@pytest.mark.asyncio
async def test_create_completion_falls_back_to_responses() -> None:
    responses_result = SimpleNamespace(
        output=[SimpleNamespace(type="function_call", call_id="c1", name="todo_add", arguments="{}")],
        output_text="",
    )
    client = FakeClient(chat_error=_bad_request("use /v1/responses"), responses_result=responses_result)
    result = await create_completion(client, model="gpt-5.6-terra", messages=[{"role": "user", "content": "hi"}],
                                     tools=[{"type": "function", "function": {"name": "todo_add", "parameters": {}}}])
    assert client.responses.calls == 1
    assert result.choices[0].message.tool_calls[0].function.name == "todo_add"
    assert client.responses.captured["tools"][0]["name"] == "todo_add"


@pytest.mark.asyncio
async def test_create_completion_reraises_unrelated_bad_request() -> None:
    client = FakeClient(chat_error=_bad_request("context length exceeded"))
    with pytest.raises(BadRequestError):
        await create_completion(client, model="gpt-4o", messages=[{"role": "user", "content": "hi"}], tools=[])
    assert client.responses.calls == 0


@pytest.mark.asyncio
async def test_create_completion_happy_path_no_fallback() -> None:
    ok = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))])
    client = FakeClient(chat_result=ok)
    result = await create_completion(client, model="gpt-4o", messages=[{"role": "user", "content": "hi"}])
    assert result is ok
    assert client.responses.calls == 0
