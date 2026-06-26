from pathlib import Path

from app.document_libraries import (
    DocumentLibrary,
    DocumentLibraryStore,
    scan_library_documents,
)
from app.file_types import document_kind, is_supported_document


def test_store_add_deduplicates_paths(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    store = DocumentLibraryStore(path=tmp_path / "libraries.json")

    first, first_added = store.add(root)
    second, second_added = store.add(root)

    assert first_added is True
    assert second_added is False
    assert second.id == first.id
    assert store.load() == [first]


def test_store_remove_library(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    store = DocumentLibraryStore(path=tmp_path / "libraries.json")
    lib, _added = store.add(root)

    store.remove(lib.id)

    assert store.load() == []


def test_scan_documents_includes_markdown_pdf_and_skips_hidden_dirs(tmp_path):
    root = tmp_path / "library"
    nested = root / "notes"
    hidden = root / ".git"
    nested.mkdir(parents=True)
    hidden.mkdir()
    (root / "README.md").write_text("# readme", encoding="utf-8")
    (nested / "design.markdown").write_text("# design", encoding="utf-8")
    (nested / "datasheet.pdf").write_bytes(b"%PDF-1.4\n")
    (nested / "image.png").write_text("not markdown", encoding="utf-8")
    (hidden / "ignored.md").write_text("# ignored", encoding="utf-8")

    docs = scan_library_documents(
        [DocumentLibrary(id="lib", name="Library", path=str(root))]
    )

    assert {Path(doc.path).name for doc in docs} == {
        "README.md",
        "design.markdown",
        "datasheet.pdf",
    }
    assert {doc.kind for doc in docs} == {"markdown", "pdf"}
    assert {doc.relative_path for doc in docs} == {
        "README.md",
        str(Path("notes") / "design.markdown"),
        str(Path("notes") / "datasheet.pdf"),
    }


def test_file_type_helpers_support_markdown_and_pdf():
    assert document_kind("note.md") == "markdown"
    assert document_kind("note.markdown") == "markdown"
    assert document_kind("datasheet.PDF") == "pdf"
    assert is_supported_document("datasheet.pdf")
    assert not is_supported_document("image.png")
