from types import SimpleNamespace

from app import file_ops
from app.document_libraries import prune_directory_names


def test_directory_pruning_preserves_order_and_relative_exclusions():
    from pathlib import Path

    names = ["keep", ".git", "private", "last"]
    identity = names
    prune_directory_names(names, Path("docs"), ["docs/private"])
    assert names is identity
    assert names == ["keep", "last"]


def test_explorer_success_exit_one_is_not_an_error(tmp_path, monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(file_ops.subprocess, "run", run)
    path = tmp_path / "a b.md"
    file_ops.show_in_explorer(path)
    assert calls == [(["explorer", "/select,", str(path)], {"check": False})]
