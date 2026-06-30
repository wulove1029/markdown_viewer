"""Tests for opening password-protected PDFs.

Covers the pure outline path (``extract_outline`` with a password) and the
widget-level prompt/retry/cancel/reuse flow in ``PdfView.load``. The modal
password dialog is replaced by an injected ``_password_prompt`` so the encrypted
path runs head-lessly.
"""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from app.pdf_view import PdfView, extract_outline

PWD = "secret"
TOC0 = [(1, "Chapter One", 0), (2, "Section 1.1", 0)]


def _make_encrypted_pdf(path, pwd=PWD, with_toc=True):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 100), "alpha beta gamma", fontsize=18)
    if with_toc:
        doc.set_toc([[1, "Chapter One", 1], [2, "Section 1.1", 1]])
    doc.save(
        str(path),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        user_pw=pwd,
        owner_pw="owner-" + pwd,
    )
    doc.close()


def _make_plain_pdf(path):
    doc = pymupdf.open()
    doc.new_page()
    doc.set_toc([[1, "Plain", 1]])
    doc.save(str(path))
    doc.close()


# --------------------------- extract_outline ---------------------------
def test_extract_outline_with_correct_password(tmp_path):
    pdf = tmp_path / "enc.pdf"
    _make_encrypted_pdf(pdf)
    assert extract_outline(pdf, PWD) == TOC0


def test_extract_outline_wrong_or_missing_password_is_empty(tmp_path):
    pdf = tmp_path / "enc.pdf"
    _make_encrypted_pdf(pdf)
    # Encrypted bookmarks must not crash get_toc(); they degrade to [].
    assert extract_outline(pdf, "nope") == []
    assert extract_outline(pdf, "") == []
    assert extract_outline(pdf) == []


def test_extract_outline_plain_still_works(tmp_path):
    pdf = tmp_path / "plain.pdf"
    _make_plain_pdf(pdf)
    assert extract_outline(pdf) == [(1, "Plain", 0)]


# --------------------------- PdfView.load ---------------------------
def test_load_prompts_once_and_unlocks(qapp, tmp_path):
    pdf = tmp_path / "enc.pdf"
    _make_encrypted_pdf(pdf)
    view = PdfView()
    calls = []
    view._password_prompt = lambda name, attempt: calls.append((name, attempt)) or PWD

    assert view.load(pdf) is True
    assert calls == [("enc.pdf", 0)]
    assert view._locked is False
    assert view._password == PWD
    assert view.page_count() == 1
    # The accepted password also unlocks the pymupdf-backed outline.
    assert view.outline() == TOC0


def test_load_retries_after_wrong_password(qapp, tmp_path):
    pdf = tmp_path / "enc.pdf"
    _make_encrypted_pdf(pdf)
    view = PdfView()
    answers = {0: "wrong-one", 1: "still-wrong", 2: PWD}
    seen = []

    def prompt(name, attempt):
        seen.append(attempt)
        return answers[attempt]

    view._password_prompt = prompt
    assert view.load(pdf) is True
    assert seen == [0, 1, 2]
    assert view._password == PWD
    assert view.page_count() == 1


def test_load_cancel_leaves_locked(qapp, tmp_path):
    pdf = tmp_path / "enc.pdf"
    _make_encrypted_pdf(pdf)
    view = PdfView()
    view._password_prompt = lambda name, attempt: None  # user cancels

    assert view.load(pdf) is False
    assert view._locked is True
    assert view._password == ""
    assert view.page_count() == 0
    assert view.outline() == []  # never authenticated


def test_load_plain_pdf_does_not_prompt(qapp, tmp_path):
    pdf = tmp_path / "plain.pdf"
    _make_plain_pdf(pdf)
    view = PdfView()
    called = []
    view._password_prompt = lambda name, attempt: called.append(1) or "ignored"

    assert view.load(pdf) is True
    assert called == []
    assert view._locked is False
    assert view._password == ""
    assert view.outline() == [(1, "Plain", 0)]


def test_reload_same_file_reuses_password(qapp, tmp_path):
    pdf = tmp_path / "enc.pdf"
    _make_encrypted_pdf(pdf)
    view = PdfView()
    prompts = {"n": 0}

    def prompt(name, attempt):
        prompts["n"] += 1
        return PWD

    view._password_prompt = prompt
    assert view.load(pdf) is True
    assert prompts["n"] == 1
    # Reloading the same path reuses the cached password — no second prompt.
    assert view.load(pdf) is True
    assert prompts["n"] == 1
    assert view.page_count() == 1


def test_opening_different_encrypted_file_prompts_again(qapp, tmp_path):
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    _make_encrypted_pdf(first, "alpha")
    _make_encrypted_pdf(second, "bravo")
    view = PdfView()
    answers = {"a.pdf": "alpha", "b.pdf": "bravo"}
    seen = []

    def prompt(name, attempt):
        seen.append(name)
        return answers[name]

    view._password_prompt = prompt
    assert view.load(first) is True
    assert view.load(second) is True  # different path -> fresh prompt
    assert seen == ["a.pdf", "b.pdf"]
    assert view._password == "bravo"


def test_load_resets_pending_page_across_files(qapp, tmp_path):
    # Regression: cancelling an encrypted PDF must not leak its remembered page
    # into the next file opened. window._open_pdf restores the remembered page
    # via restore_page() even on the locked path; that value would otherwise be
    # consumed by the *next* document's Ready event and scroll it to the wrong page.
    enc = tmp_path / "enc.pdf"
    _make_encrypted_pdf(enc)
    plain = tmp_path / "plain.pdf"
    _make_plain_pdf(plain)
    view = PdfView()
    view._password_prompt = lambda name, attempt: None  # cancel

    assert view.load(enc) is False
    view.restore_page(3)  # mimic window restoring enc's remembered page on the locked view
    assert view._pending_page == 3  # parked; enc never reaches Ready to consume it

    # Opening a different file must clear the inherited pending page and all
    # locked/failed state left over from the cancelled encrypted file.
    assert view.load(plain) is True
    assert view._pending_page is None
    assert view._locked is False
    assert view._load_failed is False
    assert view._password == ""  # enc's (never-accepted) password must not leak


def test_corrupt_pdf_is_not_labelled_password_protected(qapp, tmp_path):
    # Regression: a non-encrypted but unreadable file must not be reported as
    # "password protected" (which would tell the user to enter a non-existent pw).
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"%PDF-1.4 not really a pdf")
    view = PdfView()
    called = []
    view._password_prompt = lambda name, attempt: called.append(1) or "x"

    assert view.load(bad) is False
    assert called == []            # not encrypted -> no password prompt
    assert view._locked is False   # must NOT be flagged as password-protected
    assert view._load_failed is True
    assert view.is_locked() is False


def test_missing_pdf_is_not_labelled_password_protected(qapp, tmp_path):
    view = PdfView()
    called = []
    view._password_prompt = lambda name, attempt: called.append(1) or "x"

    assert view.load(tmp_path / "does_not_exist.pdf") is False
    assert called == []
    assert view._locked is False
    assert view._load_failed is True


def test_reopen_after_cancel_can_unlock(qapp, tmp_path):
    pdf = tmp_path / "enc.pdf"
    _make_encrypted_pdf(pdf)
    view = PdfView()

    view._password_prompt = lambda name, attempt: None
    assert view.load(pdf) is False
    assert view._locked is True

    # Reopen and supply the password this time.
    view._password_prompt = lambda name, attempt: PWD
    assert view.load(pdf) is True
    assert view._locked is False
    assert view.page_count() == 1
