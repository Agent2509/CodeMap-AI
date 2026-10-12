from __future__ import annotations

import json
from pathlib import Path

import pytest

import routers.projects as projects
from routers.upload import _project_id, _sse


def test_build_tree_nests_directories() -> None:
    tree = projects._build_tree(["pkg/a.py", "top.ts"])
    names = {n["name"]: n for n in tree}
    assert names["top.ts"]["type"] == "file"
    assert names["pkg"]["type"] == "dir"
    assert names["pkg"]["children"][0]["name"] == "a.py"


def test_project_file_reads_and_blocks_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    project_id = "p1"
    (tmp_path / project_id).mkdir()
    root = tmp_path / "root"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text("x = 1\n")
    (tmp_path / project_id / "meta.json").write_text(
        json.dumps(
            {
                "id": project_id,
                "path": str(root),
                "name": "root",
                "files": ["pkg/a.py"],
            }
        )
    )

    assert projects.project_file(project_id, "pkg/a.py")["content"] == "x = 1\n"
    with pytest.raises(Exception):
        projects.project_file(project_id, "../../etc/passwd")


def test_project_id_is_stable_and_short() -> None:
    path = Path("/tmp/some/project")
    assert _project_id(path) == _project_id(path)
    assert len(_project_id(path)) == 12


def test_delete_project_removes_dir_and_blocks_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(projects, "reset_collection", lambda _name: None)
    (tmp_path / "p1").mkdir()

    assert projects.delete_project("p1")["status"] == "deleted"
    assert not (tmp_path / "p1").exists()

    with pytest.raises(Exception):
        projects.delete_project("../escape")


def test_sse_framing() -> None:
    assert _sse({"status": "done"}).startswith("data: ")
    assert _sse({"status": "done"}).endswith("\n\n")
