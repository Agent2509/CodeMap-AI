from __future__ import annotations

import ast
import asyncio
import json
import os
import re
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any, cast

import openai
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

from agent.tools import (
    TOOL_DEFINITIONS,
    TOOL_REGISTRY,
    get_active_root,
    impact_nodes,
    set_active_project,
)

MODEL = os.getenv("AI_MODEL", "deepseek-chat")
# Free tiers run out (Groq's daily cap is 200k tokens), which would kill a demo.
# An optional second OpenAI-compatible endpoint — typically local LM Studio — is
# used automatically when the primary is rate-limited.
FALLBACK_MODEL = os.getenv("AI_FALLBACK_MODEL")
FALLBACK_BASE_URL = os.getenv("AI_FALLBACK_BASE_URL")
MAX_ITERATIONS = 8
# Free tiers (Groq especially) return 429 under bursts; retry the request opening
# before giving up so a transient spike doesn't kill the demo.
MAX_RETRIES = 3

client = AsyncOpenAI(
    base_url=os.getenv("AI_BASE_URL", "https://api.deepseek.com/v1"),
    api_key=os.getenv("AI_API_KEY") or "missing",
)

fallback_client = (
    AsyncOpenAI(
        base_url=FALLBACK_BASE_URL,
        api_key=os.getenv("AI_FALLBACK_API_KEY") or "lm-studio",
    )
    if FALLBACK_MODEL and FALLBACK_BASE_URL
    else None
)

SYSTEM_PROMPT = """You are CodeMap AI — an expert agentic code reviewer with deep codebase knowledge.

You have access to the following tools:
1. search_codebase(query) — Hybrid (semantic + keyword) search over the indexed codebase using RAG. Use this to find relevant code snippets.
2. get_file_content(filepath) — Read a specific file with line numbers.
3. find_definition(symbol) — Locate where a function, class or file is defined.
4. find_references(symbol) — Find what imports or calls a symbol.
5. get_dependency_path(a, b) — Shortest dependency path between two files or symbols.
6. impact_analysis(node) — Everything affected by a change to a file or symbol, directly and transitively.
7. generate_diagram(components) — Generate a Mermaid.js diagram from a list of components.

Your job:
- When the user first connects or asks "what is this codebase?", use search_codebase to sample several chunks, then provide a structured overview: purpose, tech stack, architecture pattern, key files, and any code smells you notice.
- When asked to review code, search for the relevant files, read them, and provide actionable feedback on: correctness, edge cases, security, performance, and style.
- When asked what breaks, what depends on something, or the impact of a change, call impact_analysis and report it directly. This is the graph's strongest answer — prefer it over guessing.
- When asked about architecture, generate Mermaid diagrams to illustrate the relationships.
- Always cite specific file paths and line numbers in your responses.
- Be opinionated but constructive. Flag real issues, skip nitpicks.

Formatting rules — the answer renders in a narrow side panel:
- Lead with a 2-3 sentence summary, then short bullet points. Do not write an essay or repeat the question.
- Prefer bullets over tables. Only use a table for a small, uniform comparison.
- Never put code containing a pipe (`|`) inside a table cell — type hints like `str | None` split the row and render broken. Reference such code in backticks on its own line instead.
- Keep answers tight by default. Expand only when the user asks for depth.

Think step by step. Use tools before answering. Never guess when you can look.
Before each tool call, state your reasoning in one short sentence.
"""

# Local models don't always use native tool calling — some emit the call as text,
# either wrapped in <tools>, as a raw {"name": ..., "arguments": ...} dict, or as a
# function-style call like search_codebase({"query": ...}). Suppress those from the
# token stream so the raw tool call never reaches the UI.
# ponytail: heuristic; if a model ever streams a matching pattern it would be hidden.
# Narrow if it ever bites.
KNOWN_TOOLS = "|".join(TOOL_REGISTRY)
TOOL_CALL_MARKER = re.compile(
    rf"<tools>|\{{\s*['\"]name['\"]\s*:|(?:{KNOWN_TOOLS})\s*[\({{]",
)
# Withhold this many trailing chars so a marker split across chunks can't leak before
# we recognise it.
HOLDBACK = 12


def _sse(payload: dict[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


# The transcript is resent on every iteration, so it is bounded: keep the opening
# turns plus the most recent tool exchanges, and cap each tool result. Without this
# a few file reads blow the provider's tokens-per-minute ceiling on their own.
KEEP_TOOL_ROUNDS = 2
MAX_TOOL_RESULT_CHARS = 1200


def _truncate_tool_result(message: dict[str, Any], max_chars: int) -> dict[str, Any]:
    if message.get("role") != "tool":
        return message
    content = str(message.get("content") or "")
    if len(content) <= max_chars:
        return message
    return {**message, "content": content[:max_chars] + "\n... [truncated]"}


def _compact_messages(
    messages: list[dict[str, Any]],
    keep_rounds: int = KEEP_TOOL_ROUNDS,
    max_chars: int = MAX_TOOL_RESULT_CHARS,
) -> list[dict[str, Any]]:
    """Trim a ReAct transcript so the request cannot trip a provider's token cap.

    A tool-using assistant message and the tool messages answering it are one
    atomic round — partial rounds are invalid, so they are dropped together.
    """
    head: list[dict[str, Any]] = []
    rounds: list[list[dict[str, Any]]] = []
    index, size = 0, len(messages)

    while index < size and not (
        messages[index].get("role") == "assistant" and messages[index].get("tool_calls")
    ):
        head.append(messages[index])
        index += 1

    while index < size:
        group = [messages[index]]
        index += 1
        while index < size and messages[index].get("role") == "tool":
            group.append(messages[index])
            index += 1
        rounds.append(group)

    kept = rounds[-keep_rounds:] if rounds else []
    compacted = head + [m for group in kept for m in group]
    return [_truncate_tool_result(m, max_chars) for m in compacted]


def _flatten_for_answer(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rewrite a tool-using transcript into plain messages.

    Providers reject a transcript that still contains tool calls when tools are not
    offered ("tool choice is none, but model called a tool" / tool_use_failed), so
    the tool exchanges are collapsed to text before we ask for a final answer.
    """
    flat: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        if role in ("system", "user"):
            flat.append({"role": role, "content": message.get("content") or ""})
        elif role == "assistant" and message.get("content"):
            flat.append({"role": "assistant", "content": message["content"]})
        elif role == "tool":
            flat.append(
                {
                    "role": "user",
                    "content": f"Tool result:\n{str(message.get('content'))[:MAX_TOOL_RESULT_CHARS]}",
                }
            )
    flat.append(
        {
            "role": "user",
            "content": "Using only the information above, give the final answer now. Do not call any tools.",
        }
    )
    return flat


def _friendly_error(exc: Exception) -> str:
    """Turn a provider error into something a user can act on."""
    text = str(exc)
    if "per day" in text or "TPD" in text:
        return (
            "The AI provider's daily token limit is used up. "
            "Wait for the quota to reset, or configure a fallback provider "
            "(AI_FALLBACK_BASE_URL / AI_FALLBACK_MODEL)."
        )
    if "per minute" in text or "TPM" in text:
        return "The AI provider is rate-limiting requests. Try again in a minute."
    if "Parsing failed" in text or "tool_use_failed" in text:
        return "The model returned a tool call the provider could not parse. Try rephrasing."
    if "Request too large" in text:
        return "That request was too large for the model. Ask about a smaller area."
    return text


def _minimal_transcript(
    messages: list[dict[str, Any]], user_prompt: str, system_prompt: str
) -> list[dict[str, Any]]:
    """Last-resort request: the question plus the gathered tool output as plain text.

    Contains no tool calls in any form, so a provider cannot reject it for an
    unparseable call.
    """
    gathered = [
        str(m.get("content"))[:MAX_TOOL_RESULT_CHARS]
        for m in messages
        if m.get("role") == "tool" and m.get("content")
    ]
    body = f"Question: {user_prompt}\n"
    if gathered:
        body += "\nContext gathered from the codebase:\n" + "\n---\n".join(gathered)
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": body + "\n\nAnswer the question using the context above."},
    ]


async def run_agent_stream(
    user_prompt: str,
    history: list[dict[str, Any]] | None = None,
    root: str | Path | None = None,
    project_id: str | None = None,
) -> AsyncGenerator[str, None]:
    # Apply the request's project here, not in the route: the route is a sync
    # function on a threadpool, and a contextvar set there never reaches the event
    # loop that drives this stream. Everything below reads what we set here.
    set_active_project(project_id, root)
    root = get_active_root()
    system_prompt = SYSTEM_PROMPT
    if root:
        system_prompt += (
            f"\nThe project root is: {root}\n"
            "Paths passed to get_file_content are relative to that root "
            "(e.g. 'src/app.py'), never absolute.\n"
        )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        *(history or []),
        {"role": "user", "content": user_prompt},
    ]
    seen_calls: set[tuple[str, str]] = set()
    active_client = client
    active_model = MODEL

    for iteration in range(MAX_ITERATIONS):
        # On the final pass, withhold the tools so the model must answer with what it
        # has gathered. Without this a tool-happy model burns the whole budget and
        # returns an empty reply.
        final_pass = iteration == MAX_ITERATIONS - 1
        request: dict[str, Any] = {"model": active_model, "stream": True}
        if final_pass:
            # Flatten the transcript too — a bare "no tools" request is rejected by
            # some providers while the history still contains tool calls.
            request["messages"] = _flatten_for_answer(messages)
        else:
            request["messages"] = cast(
                list[ChatCompletionMessageParam], _compact_messages(messages)
            )
            request["tools"] = cast(list[ChatCompletionToolParam], TOOL_DEFINITIONS)

        # Open and consume the stream inside the retry loop: providers such as Groq
        # can fail the tool_use parse *while* streaming, not only at request time.
        delay = 1.0
        backoff = 0
        # How many recovery steps remain: first drop tools, then reduce the whole
        # request to a minimal transcript. Neither is possible once chunks have been
        # streamed, because the answer would be replayed.
        recoveries = 0 if final_pass else 2
        compacted = False
        yielded = False
        content = ""
        pending = ""
        tool_calls: dict[int, dict[str, str]] = {}
        in_tools_block = False

        while True:
            try:
                stream = await active_client.chat.completions.create(**request)
                content = ""
                pending = ""
                tool_calls = {}
                in_tools_block = False

                async for chunk in stream:
                    if not chunk.choices:
                        continue
                    delta = chunk.choices[0].delta

                    # Standard tool calls
                    for tc in delta.tool_calls or []:
                        slot = tool_calls.setdefault(tc.index, {"id": "", "name": "", "arguments": ""})
                        if tc.id: slot["id"] = tc.id
                        if tc.function and tc.function.name: slot["name"] = tc.function.name
                        if tc.function and tc.function.arguments: slot["arguments"] += tc.function.arguments

                    if delta.content:
                        content += delta.content
                        if in_tools_block:
                            continue
                        if TOOL_CALL_MARKER.search(content):
                            in_tools_block = True
                            pending = ""
                            continue
                        pending += delta.content
                        if len(pending) > HOLDBACK:
                            yielded = True
                            yield _sse({"type": "chunk", "text": pending[:-HOLDBACK]})
                            pending = pending[-HOLDBACK:]
                break
            except openai.BadRequestError as exc:
                # Groq rejects an unparseable tool call with 400 tool_use_failed,
                # including on the final pass. Step down to a safer request rather
                # than surfacing a raw provider error: first drop tools, then send a
                # minimal transcript with no tool exchange at all.
                if yielded or recoveries <= 0:
                    raise RuntimeError(_friendly_error(exc)) from exc
                recoveries -= 1
                if recoveries == 1:
                    request["messages"] = _flatten_for_answer(messages)
                    request.pop("tools", None)
                    note = "Tool call could not be parsed; answering from gathered context..."
                else:
                    request["messages"] = _minimal_transcript(messages, user_prompt, system_prompt)
                    request.pop("tools", None)
                    note = "Retrying with a trimmed request..."
                yield _sse({"type": "thought", "text": f"⏳ {note}"})
                continue
            except openai.RateLimitError as exc:
                # Switch to the fallback provider once rather than dying — a spent
                # free-tier quota should not end the session.
                if fallback_client is not None and active_client is not fallback_client:
                    active_client = fallback_client
                    active_model = FALLBACK_MODEL or ""
                    request["model"] = active_model
                    yield _sse(
                        {
                            "type": "thought",
                            "text": f"⏳ Primary provider rate-limited; switching to {active_model}.",
                        }
                    )
                    continue
                if backoff >= MAX_RETRIES or yielded:
                    raise RuntimeError(_friendly_error(exc)) from exc
                backoff += 1
                yield _sse({"type": "thought", "text": f"⏳ Provider busy, retrying in {delay:.0f}s..."})
                await asyncio.sleep(delay)
                delay *= 2
            except openai.APIStatusError as exc:
                # 413: the request exceeded the provider's token ceiling. Drop the
                # older tool rounds and retry with a much smaller transcript.
                if exc.status_code == 413 and not compacted:
                    compacted = True
                    if final_pass:
                        request["messages"] = _minimal_transcript(messages, user_prompt, system_prompt)
                    else:
                        request["messages"] = cast(
                            list[ChatCompletionMessageParam],
                            _compact_messages(messages, keep_rounds=1, max_chars=600),
                        )
                    yield _sse(
                        {
                            "type": "thought",
                            "text": "⏳ Context too large for the model; retrying with a trimmed transcript...",
                        }
                    )
                    continue
                if exc.status_code >= 500 and backoff < MAX_RETRIES and not yielded:
                    backoff += 1
                    yield _sse({"type": "thought", "text": f"⏳ Provider busy, retrying in {delay:.0f}s..."})
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                raise RuntimeError(_friendly_error(exc)) from exc

        if not in_tools_block and pending:
            yield _sse({"type": "chunk", "text": pending})


        if not tool_calls:
            # Fallback for local models that emit a raw tool dict instead of native
            # tool calls. Brace-match so nested arguments survive; literal_eval
            # accepts Qwen's single quotes, json.loads covers true/false/null.
            start = content.find("{")
            candidate = ""
            if start != -1:
                depth = 0
                for i in range(start, len(content)):
                    if content[i] == "{":
                        depth += 1
                    elif content[i] == "}":
                        depth -= 1
                        if depth == 0:
                            candidate = content[start : i + 1]
                            break
            if candidate:
                try:
                    try:
                        raw_call = ast.literal_eval(candidate)
                    except (ValueError, SyntaxError):
                        raw_call = json.loads(candidate)
                    if isinstance(raw_call, dict) and "name" in raw_call and "arguments" in raw_call:
                        tool_calls[0] = {
                            "id": "call_fallback",
                            "name": raw_call["name"],
                            "arguments": json.dumps(raw_call["arguments"]) if isinstance(raw_call["arguments"], dict) else raw_call["arguments"]
                        }
                except (ValueError, SyntaxError, json.JSONDecodeError):
                    pass

        if not tool_calls:
            break

        # Surface the ReAct step in the UI. Native tool-calling models often emit no
        # text before the call, so only show a thought when the model actually gave
        # one — an empty filler line repeated per step is just noise.
        marker = TOOL_CALL_MARKER.search(content)
        thought_text = (content[: marker.start()] if marker else content).strip()
        if thought_text:
            yield _sse({"type": "thought", "text": thought_text})
        for call in tool_calls.values():
            try:
                action_args = json.loads(call["arguments"] or "{}")
            except json.JSONDecodeError:
                action_args = {}
            yield _sse({"type": "action", "data": {"name": call["name"], "arguments": action_args}})

        messages.append(
            {
                "role": "assistant",
                "content": content or None,
                "tool_calls": [
                    {
                        "id": c["id"],
                        "type": "function",
                        "function": {"name": c["name"], "arguments": c["arguments"]},
                    }
                    for c in tool_calls.values()
                ],
            }
        )

        for call in tool_calls.values():
            signature = (call["name"], call["arguments"])
            if signature in seen_calls:
                result = "Duplicate call skipped. Use a different tool or arguments, or answer now."
            else:
                seen_calls.add(signature)
                result = _execute(call["name"], call["arguments"])
            messages.append(
                {"role": "tool", "tool_call_id": call["id"], "content": result}
            )
            yield _sse({"type": "observation", "data": result})

            # An impact analysis is the one answer worth drawing: hand the UI the
            # affected node ids so the graph can light up the blast radius.
            if call["name"] == "impact_analysis":
                try:
                    argument = json.loads(call["arguments"] or "{}")
                except json.JSONDecodeError:
                    argument = {}
                node = argument.get("node")
                if isinstance(node, str):
                    ids = impact_nodes(node)
                    if ids:
                        yield _sse({"type": "highlight", "nodes": ids})

    yield _sse({"type": "done"})


def _execute(name: str, arguments: str) -> str:
    func = TOOL_REGISTRY.get(name)
    if func is None:
        return f"Unknown tool: {name}"
    try:
        result = func(**json.loads(arguments or "{}"))
    except Exception as exc:  # ponytail: surface tool errors to the model, don't crash stream
        return f"Error: {exc}"
    return result if isinstance(result, str) else json.dumps(result)
