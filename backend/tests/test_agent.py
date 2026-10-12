from __future__ import annotations

import asyncio
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

from agent import react_agent, tools


class _Fn:
    def __init__(self, name: str | None = None, arguments: str | None = None) -> None:
        self.name = name
        self.arguments = arguments


class _ToolCall:
    def __init__(self, index: int, id: str | None = None, name: str | None = None,
                 arguments: str | None = None) -> None:
        self.index = index
        self.id = id
        self.function = _Fn(name, arguments)


class _Delta:
    def __init__(self, content: str | None = None, tool_calls: list[_ToolCall] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []


class _Chunk:
    def __init__(self, delta: _Delta) -> None:
        self.choices = [types.SimpleNamespace(delta=delta)]


class _FakeStream:
    def __init__(self, chunks: list[_Chunk]) -> None:
        self._chunks = chunks
        self._i = 0

    def __aiter__(self) -> "_FakeStream":
        return self

    async def __anext__(self) -> _Chunk:
        if self._i >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._i]
        self._i += 1
        return chunk


class _FakeCompletions:
    def __init__(self, streams: list[_FakeStream]) -> None:
        self._streams = streams
        self._n = 0
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> _FakeStream:
        self.calls.append(kwargs)
        stream = self._streams[self._n]
        self._n += 1
        return stream


class _FakeClient:
    def __init__(self, streams: list[_FakeStream]) -> None:
        self.chat = types.SimpleNamespace(completions=_FakeCompletions(streams))


class _ClientWith:
    def __init__(self, completions: object) -> None:
        self.chat = types.SimpleNamespace(completions=completions)


def _bad_request_error() -> Exception:
    import httpx
    import openai

    request = httpx.Request("POST", "https://api.example/v1/chat/completions")
    return openai.BadRequestError(
        "Parsing failed. The model generated output that could not be parsed.",
        response=httpx.Response(400, request=request),
        body=None,
    )


class _BadToolCallThenAnswer:
    """Rejects any request carrying tools, then streams a plain answer."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> _FakeStream:
        self.calls.append(kwargs)
        if "tools" in kwargs:
            raise _bad_request_error()
        return _FakeStream([_Chunk(_Delta(content="Answer without tools."))])


def _drain(gen) -> list[str]:
    async def run() -> list[str]:
        return [line async for line in gen]

    return asyncio.run(run())


@pytest.fixture(autouse=True)
def _stub_tools(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # Keep the tests offline and instant: no Chroma, no embedding model.
    monkeypatch.setitem(react_agent.TOOL_REGISTRY, "search_codebase", lambda query: f"hit:{query}")
    yield


@pytest.fixture(autouse=True)
def _reset_project_context() -> Iterator[None]:
    # The active project is per-context now, so a test that sets one must not leak
    # it into the next test running in the same thread.
    yield
    tools.set_active_project(None, None)


def test_marker_matches_every_tool_call_form() -> None:
    for text in [
        "<tools>",
        '{"name": "search_codebase"',
        "search_codebase({",
        'search_codebase{"query"',
    ]:
        assert react_agent.TOOL_CALL_MARKER.search(text), text


def test_execute_unknown_tool_is_surfaced_not_raised() -> None:
    assert react_agent._execute("nope", "{}") == "Unknown tool: nope"


def test_execute_bad_arguments_does_not_crash() -> None:
    result = react_agent._execute("generate_diagram", "not json")
    assert result.startswith("Error:")


def _payloads(lines: list[str]) -> list[dict]:
    import json

    return [json.loads(line[len("data: ") :]) for line in lines if line.startswith("data: ")]


def test_raw_tool_json_never_reaches_the_client(monkeypatch: pytest.MonkeyPatch) -> None:
    # A local-style model that emits the call as text rather than a native call.
    raw = 'I will search.{"name": "search_codebase", "arguments": {"query": "auth"}}'
    streams = [
        _FakeStream([_Chunk(_Delta(content=raw))]),
        _FakeStream([_Chunk(_Delta(content="Here is the answer."))]),
    ]
    monkeypatch.setattr(react_agent, "client", _FakeClient(streams))

    events = _payloads(_drain(react_agent.run_agent_stream("find auth")))
    answer = "".join(e.get("text", "") for e in events if e["type"] == "chunk")

    # The raw call must not leak into answer text; it may only appear as a
    # structured action event.
    assert "search_codebase" not in answer
    assert '{"name"' not in answer
    assert "Here is the answer." in answer
    assert any(e["type"] == "action" and e["data"]["name"] == "search_codebase" for e in events)


def test_native_tool_call_emits_action_and_observation(monkeypatch: pytest.MonkeyPatch) -> None:
    streams = [
        _FakeStream([
            _Chunk(_Delta(tool_calls=[_ToolCall(0, id="c1", name="search_codebase",
                                                arguments='{"query": "auth"}')])),
        ]),
        _FakeStream([_Chunk(_Delta(content="Done."))]),
    ]
    monkeypatch.setattr(react_agent, "client", _FakeClient(streams))

    lines = _drain(react_agent.run_agent_stream("find auth"))

    assert any('"type": "action"' in line and "search_codebase" in line for line in lines)
    assert any('"type": "observation"' in line and "hit:auth" in line for line in lines)
    assert any('"type": "done"' in line for line in lines)


class _RaisingStream:
    """Async stream that fails while being consumed, like Groq's tool_use_failed."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    def __aiter__(self) -> "_RaisingStream":
        return self

    async def __anext__(self) -> _Chunk:
        raise self._exc


class _MidStreamFailureThenAnswer:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if "tools" in kwargs:
            return _RaisingStream(_bad_request_error())
        return _FakeStream([_Chunk(_Delta(content="Recovered answer."))])


def test_mid_stream_tool_failure_is_recovered(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = _MidStreamFailureThenAnswer()
    monkeypatch.setattr(react_agent, "client", _ClientWith(completions))

    events = _payloads(_drain(react_agent.run_agent_stream("go")))
    answer = "".join(e.get("text", "") for e in events if e["type"] == "chunk")

    assert "Recovered answer." in answer
    assert "tools" not in completions.calls[-1]


def _too_large_error() -> Exception:
    import httpx
    import openai

    request = httpx.Request("POST", "https://api.example/v1/chat/completions")
    return openai.APIStatusError(
        "Request too large for model on tokens per minute (TPM): Limit 8000",
        response=httpx.Response(413, request=request),
        body=None,
    )


class _TooLargeThenOk:
    """Rejects the first (oversized) request with a 413, then streams an answer."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> _FakeStream:
        self.calls.append(kwargs)
        if len(self.calls) == 1:
            raise _too_large_error()
        return _FakeStream([_Chunk(_Delta(content="Trimmed answer."))])


def test_compact_messages_keeps_head_and_recent_rounds() -> None:
    messages: list[dict] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
    ]
    for i in range(4):
        messages.append(
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": f"c{i}", "type": "function", "function": {"name": "t", "arguments": "{}"}}
                ],
            }
        )
        messages.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 5000})

    out = react_agent._compact_messages(messages, keep_rounds=2, max_chars=100)

    assert [m["role"] for m in out][:2] == ["system", "user"]
    assert [m["role"] for m in out].count("tool") == 2
    # Every retained tool result is capped, so the request stays small.
    assert all(
        len(str(m["content"])) <= 100 + len("\n... [truncated]")
        for m in out
        if m["role"] == "tool"
    )


def test_413_request_too_large_is_retried_with_trimmed_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    completions = _TooLargeThenOk()
    monkeypatch.setattr(react_agent, "client", _ClientWith(completions))

    events = _payloads(_drain(react_agent.run_agent_stream("go")))
    answer = "".join(e.get("text", "") for e in events if e["type"] == "chunk")

    assert len(completions.calls) == 2
    assert "Trimmed answer." in answer


def _rate_limit_error(message: str = "Rate limit reached on tokens per day (TPD): Limit 200000") -> Exception:
    import httpx
    import openai

    request = httpx.Request("POST", "https://api.example/v1/chat/completions")
    return openai.RateLimitError(
        message, response=httpx.Response(429, request=request), body=None
    )


def test_friendly_error_explains_daily_limit() -> None:
    message = react_agent._friendly_error(_rate_limit_error())
    assert "daily token limit" in message.lower()
    # The raw provider payload must not reach the user.
    assert "TPD" not in message


def test_friendly_error_explains_tool_parse_failure() -> None:
    message = react_agent._friendly_error(_bad_request_error())
    assert "could not parse" in message


def test_minimal_transcript_has_no_tool_exchange() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "a", "type": "function", "function": {"name": "t", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "a", "content": "GATHERED FACT"},
    ]

    out = react_agent._minimal_transcript(messages, "the question", "sys")

    roles = [m["role"] for m in out]
    assert roles == ["system", "user"]
    assert all("tool_calls" not in m for m in out)
    assert "GATHERED FACT" in str(out[1]["content"])
    assert "the question" in str(out[1]["content"])


class _RateLimitedThenFallback:
    """Primary always 429s; the fallback answers."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create(self, **kwargs: object) -> _FakeStream:
        self.calls.append(kwargs)
        model = str(kwargs.get("model"))
        if model == "primary-model":
            raise _rate_limit_error()
        return _FakeStream([_Chunk(_Delta(content="Fallback answer."))])


def test_rate_limit_switches_to_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = _RateLimitedThenFallback()
    fallback = _ClientWith(completions)
    monkeypatch.setattr(react_agent, "client", _ClientWith(completions))
    monkeypatch.setattr(react_agent, "fallback_client", fallback)
    monkeypatch.setattr(react_agent, "FALLBACK_MODEL", "local-model")
    monkeypatch.setattr(react_agent, "MODEL", "primary-model")

    events = _payloads(_drain(react_agent.run_agent_stream("go")))
    answer = "".join(e.get("text", "") for e in events if e["type"] == "chunk")

    assert "Fallback answer." in answer
    assert any("switching to local-model" in str(e.get("text", "")) for e in events)


def test_rate_limit_without_fallback_surfaces_friendly_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _AlwaysLimited:
        async def create(self, **kwargs: object) -> _FakeStream:
            raise _rate_limit_error()

    monkeypatch.setattr(react_agent, "client", _ClientWith(_AlwaysLimited()))
    monkeypatch.setattr(react_agent, "fallback_client", None)

    with pytest.raises(RuntimeError, match="daily token limit"):
        _drain(react_agent.run_agent_stream("go"))


def test_flatten_for_answer_removes_tool_exchanges() -> None:
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "q"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "a1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
            ],
        },
        {"role": "tool", "tool_call_id": "a1", "content": "OBSERVED"},
    ]

    flat = react_agent._flatten_for_answer(messages)

    # No tool calls or tool-role messages may survive, or providers reject the request.
    assert all("tool_calls" not in m for m in flat)
    assert all(m["role"] != "tool" for m in flat)
    assert any("OBSERVED" in str(m.get("content")) for m in flat)


def test_malformed_tool_call_falls_back_to_answering(monkeypatch: pytest.MonkeyPatch) -> None:
    completions = _BadToolCallThenAnswer()
    monkeypatch.setattr(react_agent, "client", _ClientWith(completions))

    events = _payloads(_drain(react_agent.run_agent_stream("go")))
    answer = "".join(e.get("text", "") for e in events if e["type"] == "chunk")

    # First request carried tools and was rejected; the retry dropped them.
    assert "tools" in completions.calls[0]
    assert "tools" not in completions.calls[-1]
    assert "Answer without tools." in answer


def test_final_pass_withholds_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    # Always request a tool so the loop runs the full budget; the last request must
    # omit tools so the model is forced to answer instead of looping forever.
    def tool_stream() -> _FakeStream:
        return _FakeStream([
            _Chunk(_Delta(tool_calls=[_ToolCall(0, id="c", name="search_codebase",
                                                arguments='{"query": "q"}')])),
        ])

    streams = [tool_stream() for _ in range(react_agent.MAX_ITERATIONS)]
    fake = _FakeClient(streams)
    monkeypatch.setattr(react_agent, "client", fake)

    _drain(react_agent.run_agent_stream("go"))

    calls = fake.chat.completions.calls
    assert "tools" in calls[0]
    assert "tools" not in calls[-1]


def test_get_file_content_resolves_against_active_project_root(
    tmp_path: Path,
) -> None:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_text("print('hi')\n")
    tools.set_project_root(root)

    out = tools.get_file_content("src/app.py")

    assert "print('hi')" in out


def test_get_file_content_blocks_traversal_outside_project(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    root.mkdir()
    (tmp_path / "secret.txt").write_text("nope")
    tools.set_project_root(root)

    with pytest.raises(ValueError, match="path traversal"):
        tools.get_file_content("../secret.txt")


def test_get_file_content_without_active_project_falls_back_to_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools.set_project_root(None)
    monkeypatch.setattr(tools, "PROJECT_ROOT", Path("/nonexistent-root"))

    with pytest.raises(ValueError, match="path traversal"):
        tools.get_file_content("../../../etc/passwd")


def test_concurrent_streams_keep_their_own_project_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two chats about different projects must not share the active root.

    Both streams call get_file_content at the same moment, so the root each tool
    sees is the one the other request would have clobbered if the state were global.
    """
    root_a, root_b = tmp_path / "a", tmp_path / "b"
    for root in (root_a, root_b):
        root.mkdir()
        (root / "note.txt").write_text("x\n")

    seen: list[Path | None] = []
    real_get_file_content = tools.get_file_content

    def spy(filepath: str, **kwargs: object) -> str:
        # Read mid-stream, after the sibling request has run its first turn.
        seen.append(tools.get_active_root())
        return str(real_get_file_content(filepath, **kwargs))

    monkeypatch.setitem(react_agent.TOOL_REGISTRY, "get_file_content", spy)

    class _ToolThenAnswer:
        async def create(self, **kwargs: object) -> _FakeStream:
            messages = kwargs["messages"]
            assert isinstance(messages, list)
            if any(m.get("role") == "tool" for m in messages):
                return _FakeStream([_Chunk(_Delta(content="done"))])
            # Yield once so the other request interleaves before this one runs its
            # tool; with shared state the wrong root would be recorded below.
            await asyncio.sleep(0)
            return _FakeStream(
                [
                    _Chunk(
                        _Delta(
                            tool_calls=[
                                _ToolCall(
                                    0,
                                    id="c1",
                                    name="get_file_content",
                                    arguments='{"filepath": "note.txt"}',
                                )
                            ]
                        )
                    )
                ]
            )

    monkeypatch.setattr(react_agent, "client", _ClientWith(_ToolThenAnswer()))

    async def run() -> None:
        async def consume(stream: object) -> None:
            async for _ in stream:  # type: ignore[attr-defined]
                pass

        await asyncio.gather(
            consume(react_agent.run_agent_stream("q", root=str(root_a), project_id="a")),
            consume(react_agent.run_agent_stream("q", root=str(root_b), project_id="b")),
        )

    asyncio.run(run())

    assert sorted(str(r) for r in seen) == sorted(
        [str(root_a.resolve()), str(root_b.resolve())]
    )


_GRAPH = {
    "nodes": [
        {"id": "pkg/a.py", "type": "file", "name": "a.py"},
        {"id": "pkg/b.py", "type": "file", "name": "b.py"},
        {
            "id": "pkg/a.py:function:helper",
            "type": "function",
            "name": "helper",
            "file_path": "pkg/a.py",
            "start_line": 1,
            "end_line": 2,
        },
        {
            "id": "pkg/b.py:function:main",
            "type": "function",
            "name": "main",
            "file_path": "pkg/b.py",
            "start_line": 1,
            "end_line": 3,
        },
    ],
    "links": [
        {"source": "pkg/a.py", "target": "pkg/a.py:function:helper"},
        {"source": "pkg/b.py", "target": "pkg/b.py:function:main"},
        {
            "source": "pkg/b.py:function:main",
            "target": "pkg/a.py:function:helper",
            "kind": "call",
        },
        {"source": "pkg/b.py", "target": "pkg/a.py", "kind": "import"},
    ],
}


@pytest.fixture
def graph_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    project_dir = tmp_path / "proj1"
    project_dir.mkdir()
    (project_dir / "graph.json").write_text(json.dumps(_GRAPH))
    monkeypatch.setattr(tools, "PROJECTS_DIR", tmp_path)
    tools.set_active_project("proj1", None)


def test_find_definition_reports_location(graph_project: None) -> None:
    out = tools.find_definition("helper")
    assert "helper" in out
    assert "pkg/a.py:1-2" in out


def test_find_definition_handles_unknown_symbol(graph_project: None) -> None:
    assert "No definition found" in tools.find_definition("does_not_exist")


def test_find_references_follows_call_edges(graph_project: None) -> None:
    out = tools.find_references("helper")
    assert "main" in out


def test_get_dependency_path_between_files(graph_project: None) -> None:
    out = tools.get_dependency_path("b.py", "a.py")
    assert "pkg/b.py" in out
    assert "pkg/a.py" in out


def test_get_dependency_path_missing_symbol(graph_project: None) -> None:
    assert "Could not find" in tools.get_dependency_path("b.py", "nope.xyz")
