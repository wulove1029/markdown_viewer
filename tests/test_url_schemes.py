import subprocess
import sys


def test_document_scheme_registers_before_application_and_is_idempotent():
    # Global Qt registration cannot be reset; isolate it from the suite's qapp.
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", """
from app.url_schemes import register_document_schemes
from PySide6.QtWebEngineCore import QWebEngineUrlScheme as Scheme
register_document_schemes()
register_document_schemes()
scheme = Scheme.schemeByName(b'wikilink')
assert scheme.name() == b'wikilink'
assert scheme.syntax() == Scheme.Syntax.Path
for flag in (Scheme.Flag.LocalScheme, Scheme.Flag.LocalAccessAllowed, Scheme.Flag.CorsEnabled):
    assert scheme.flags() & flag
print('registered before QApplication')
"""], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "registered before QApplication" in result.stdout
