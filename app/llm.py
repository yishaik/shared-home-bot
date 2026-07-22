"""LLM call helper with an automatic Chat Completions -> Responses API fallback.

Some models (e.g. GPT-5.x reasoning models) reject function tools on
`/v1/chat/completions` and require `/v1/responses`. Instead of pinning the model,
we try Chat Completions first and transparently fall back to the Responses API,
adapting the result back into the Chat-Completions shape the caller already reads
(`response.choices[0].message` with `.content` and `.tool_calls`).
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

from openai import BadRequestError

log = logging.getLogger("homebot.llm")


def needs_responses_fallback(exc: Exception) -> bool:
    """True when a Chat Completions error means we should retry via Responses."""
    text = str(getattr(exc, "message", "") or exc).lower()
    return "/v1/responses" in text or "reasoning_effort" in text or "use responses" in text


def to_responses_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Chat-Completions tool specs -> flattened Responses tool specs."""
    out: list[dict[str, Any]] = []
    for tool in tools or []:
        fn = tool.get("function", tool)
        out.append({
            "type": "function",
            "name": fn.get("name"),
            "description": fn.get("description", ""),
            "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return out


def to_responses_input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Chat-Completions message list -> Responses `input` items."""
    items: list[dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""
        if role == "tool":
            items.append({
                "type": "function_call_output",
                "call_id": msg.get("tool_call_id"),
                "output": content,
            })
            continue
        if role == "assistant" and msg.get("tool_calls"):
            if content:
                items.append({"role": "assistant", "content": content})
            for call in msg["tool_calls"]:
                fn = call.get("function", {})
                items.append({
                    "type": "function_call",
                    "call_id": call.get("id"),
                    "name": fn.get("name"),
                    "arguments": fn.get("arguments") or "{}",
                })
            continue
        # system / user / plain assistant
        items.append({"role": role, "content": content})
    return items


def adapt_responses_output(response: Any) -> SimpleNamespace:
    """Responses API result -> object shaped like a Chat Completion choice."""
    text_parts: list[str] = []
    tool_calls: list[SimpleNamespace] = []
    for item in getattr(response, "output", None) or []:
        item_type = getattr(item, "type", None)
        if item_type == "function_call":
            tool_calls.append(SimpleNamespace(
                id=getattr(item, "call_id", None) or getattr(item, "id", None),
                type="function",
                function=SimpleNamespace(
                    name=getattr(item, "name", ""),
                    arguments=getattr(item, "arguments", "") or "{}",
                ),
            ))
        elif item_type == "message":
            for chunk in getattr(item, "content", None) or []:
                piece = getattr(chunk, "text", None)
                if piece:
                    text_parts.append(piece)
    content = "".join(text_parts) or (getattr(response, "output_text", "") or "")
    message = SimpleNamespace(content=content, tool_calls=tool_calls or None)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def is_reasoning_model(model: str) -> bool:
    """GPT-5.x and o-series are reasoning models served through the Responses API."""
    m = (model or "").lower()
    return m.startswith("gpt-5") or m.startswith(("o1", "o3", "o4"))


async def _create_response(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    reasoning_effort: str | None,
) -> Any:
    """Native /v1/responses call, adapted back to the Chat-Completions shape.
    No temperature (reasoning models reject it). Stateless: the full transcript is
    resent each turn, so no server-side state / previous_response_id is needed."""
    kwargs: dict[str, Any] = {"model": model, "input": to_responses_input(messages)}
    if tools:
        kwargs["tools"] = to_responses_tools(tools)
    if reasoning_effort:
        kwargs["reasoning"] = {"effort": reasoning_effort}
    response = await client.responses.create(**kwargs)
    return adapt_responses_output(response)


async def create_completion(
    client: Any,
    *,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    reasoning_effort: str = "low",
) -> Any:
    """Run one agent turn. Reasoning models (GPT-5.x/o-series) go straight to the
    Responses API with a reasoning effort; other models use Chat Completions with a
    transparent Responses fallback. The result always exposes `.choices[0].message`."""
    if is_reasoning_model(model):
        return await _create_response(
            client, model=model, messages=messages, tools=tools, reasoning_effort=reasoning_effort
        )

    kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": temperature}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    try:
        return await client.chat.completions.create(**kwargs)
    except BadRequestError as exc:
        if not needs_responses_fallback(exc):
            raise
        log.warning("model=%s rejects tools on chat.completions; falling back to Responses API", model)
        return await _create_response(
            client, model=model, messages=messages, tools=tools, reasoning_effort=reasoning_effort
        )
