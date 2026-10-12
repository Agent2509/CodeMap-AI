from __future__ import annotations

from agent import react_agent
from agent.tools import TOOL_DEFINITIONS
from routers.chat import MAX_HISTORY, MAX_HISTORY_CHARS, _trim_history

# Groq's free tier caps a request at 8000 tokens; at roughly 4 chars per token
# that is ~32000 chars. Assert a worst-case request stays comfortably under it.
REQUEST_CHAR_BUDGET = 24000


def test_trim_history_caps_count_and_size() -> None:
    history = [{"role": "assistant", "content": "x" * 5000} for _ in range(20)]

    trimmed = _trim_history(history)

    assert len(trimmed) == MAX_HISTORY
    assert all(len(str(m["content"])) <= MAX_HISTORY_CHARS + len("\n... [truncated]") for m in trimmed)


def test_worst_case_request_stays_under_budget() -> None:
    # Full history plus the largest transcript the agent can accumulate.
    history = [
        {"role": "assistant", "content": "y" * 5000} for _ in range(MAX_HISTORY)
    ]
    messages: list[dict] = [
        {"role": "system", "content": react_agent.SYSTEM_PROMPT},
        *_trim_history(history),
        {"role": "user", "content": "explain how these files connect"},
    ]
    for i in range(3):
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": f"c{i}",
                        "type": "function",
                        "function": {"name": "get_file_content", "arguments": "{}"},
                    }
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "z" * 20000})

    compacted = react_agent._compact_messages(messages)
    tool_schema = len(str(TOOL_DEFINITIONS))
    total_chars = sum(len(str(m.get("content") or "")) for m in compacted) + tool_schema

    assert total_chars < REQUEST_CHAR_BUDGET, total_chars
