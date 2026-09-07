"""Persisted reading preferences shared by the preview and settings dialog."""

WIDTH_KEY = "reading_width"
SPACING_KEY = "reading_spacing"
WIDTHS = {"comfortable": "860px", "wide": "1200px", "full": "100%"}
SPACINGS = {"compact": "1.6", "comfortable": "1.85", "relaxed": "2.1"}


def reading_css(width="comfortable", spacing="comfortable"):
    width = WIDTHS.get(width, WIDTHS["comfortable"])
    spacing = SPACINGS.get(spacing, SPACINGS["comfortable"])
    return ("@media screen { body { --reading-width: " + width
            + "; --reading-line-height: " + spacing + "; } }")
