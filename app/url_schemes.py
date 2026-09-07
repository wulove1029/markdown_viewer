"""Register document navigation schemes before QApplication is constructed."""


def register_document_schemes():
    from PySide6.QtWebEngineCore import QWebEngineUrlScheme
    for name in (b"wikilink",):
        if QWebEngineUrlScheme.schemeByName(name).name():
            continue
        scheme = QWebEngineUrlScheme(name)
        scheme.setSyntax(QWebEngineUrlScheme.Syntax.Path)
        scheme.setFlags(QWebEngineUrlScheme.Flag.LocalScheme
                        | QWebEngineUrlScheme.Flag.LocalAccessAllowed
                        | QWebEngineUrlScheme.Flag.CorsEnabled)
        QWebEngineUrlScheme.registerScheme(scheme)
