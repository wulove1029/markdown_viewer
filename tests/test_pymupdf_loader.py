import builtins

from app import pdf_annotation_writer, pdf_embedded_annotations, pdf_view, pymupdf_loader


def test_pdf_consumers_share_one_loader_and_cache():
    loader = pymupdf_loader.load_pymupdf
    assert pdf_view._pymupdf is loader
    assert pdf_embedded_annotations._pymupdf is loader
    assert pdf_annotation_writer._pymupdf is loader
    assert loader() is loader()


def test_failed_native_import_is_cached_and_logged(monkeypatch, caplog):
    original = builtins.__import__
    attempts = []

    def unavailable(name, *args, **kwargs):
        if name == "pymupdf":
            attempts.append(name)
            raise ImportError("native library unavailable")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(pymupdf_loader, "_module", pymupdf_loader._UNSET)
    monkeypatch.setattr(builtins, "__import__", unavailable)
    assert pymupdf_loader.load_pymupdf() is None
    assert pymupdf_loader.load_pymupdf() is None
    assert attempts == ["pymupdf"]
    assert "PyMuPDF is unavailable" in caplog.text
