from __future__ import annotations

from pathlib import Path

import pytest

from routers import upload
from routers.upload import GIT_URL_RE, _iter_files


def test_iter_files_skips_secrets_and_tool_output(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n")
    (tmp_path / "notes.md").write_text("hello\n")

    # Credentials: must never reach the vector store or the model's context.
    (tmp_path / ".env").write_text("AI_API_KEY=secret\n")
    (tmp_path / ".env.example").write_text("AI_API_KEY=\n")
    (tmp_path / "id_rsa").write_text("key\n")
    (tmp_path / "server.pem").write_text("cert\n")
    (tmp_path / ".netrc").write_text("machine x\n")

    # Generated output and dependencies.
    for junk in ("graphify-out", "node_modules", ".git", ".pytest_cache", "chroma_data"):
        (tmp_path / junk).mkdir()
        (tmp_path / junk / "junk.txt").write_text("junk\n")

    found = {f.relative_to(tmp_path).as_posix() for f in _iter_files(tmp_path)}

    assert found == {"src/app.py", "notes.md"}


def test_iter_files_keeps_ordinary_source(tmp_path: Path) -> None:
    (tmp_path / "app.ts").write_text("export const A = 1;\n")
    (tmp_path / "Dockerfile").write_text("FROM python\n")

    found = {f.name for f in _iter_files(tmp_path)}

    assert found == {"app.ts", "Dockerfile"}


def test_iter_files_skips_runtime_dirs_inside_the_root(tmp_path: Path) -> None:
    for junk in ("clones", "graphify-out", "node_modules"):
        (tmp_path / junk / "third_party").mkdir(parents=True)
        (tmp_path / junk / "third_party" / "x.py").write_text("x = 1\n")
    (tmp_path / "keep.py").write_text("y = 1\n")

    assert {f.name for f in _iter_files(tmp_path)} == {"keep.py"}


def test_iter_files_does_not_skip_a_root_that_merely_sits_in_a_skipped_dir(
    tmp_path: Path,
) -> None:
    # Regression: skip names were matched against the absolute path, so a repo
    # living under .../projects/myapp indexed as zero files.
    root = tmp_path / "projects" / "myapp"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n")

    assert {f.name for f in _iter_files(root)} == {"a.py"}


def test_iter_files_can_index_a_clone_destination(tmp_path: Path) -> None:
    # `clones` is a skip dir, but the clone destination *is* under it and must
    # still be indexable.
    dest = tmp_path / "clones" / "abc123"
    dest.mkdir(parents=True)
    (dest / "main.py").write_text("y = 1\n")

    assert {f.name for f in _iter_files(dest)} == {"main.py"}


# The URL check is the only thing between a user-supplied string and a git
# subprocess, so pin both directions.
@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/owner/repo",
        "https://github.com/owner/repo.git",
        "http://gitlab.example.com/group/sub/repo.git",
        "git@github.com:owner/repo.git",
        "git@github.com:owner/repo",
    ],
)
def test_clone_url_accepts_real_git_urls(url: str) -> None:
    assert GIT_URL_RE.match(url) is not None


@pytest.mark.parametrize(
    "url",
    [
        "",
        "not a url",
        "file:///etc/passwd",
        "--upload-pack=/bin/sh",
        "https://github.com/a/b; rm -rf /",
        "https://github.com/a/b --upload-pack=x",
        "https://github.com/a/b|c",
        "javascript:alert(1)",
        "$(whoami)",
        "`whoami`",
        "https://github.com/a/b\nhttps://evil.com/x",
    ],
)
def test_clone_url_rejects_anything_else(url: str) -> None:
    assert GIT_URL_RE.match(url) is None


def test_status_stream_drops_the_task_once_finished() -> None:
    # Without this, TASKS grows for the life of the process — one entry per upload.
    upload.TASKS["t-done"] = {"status": "done", "progress": 100}

    lines = list(upload._status_stream("t-done"))

    assert any('"done"' in line for line in lines)
    assert "t-done" not in upload.TASKS


def test_status_stream_reports_an_unknown_task() -> None:
    lines = list(upload._status_stream("nope"))

    assert any("unknown task" in line for line in lines)
