"""Painting of Acrobat-authored (embedded) PDF annotations onto the page view.

``PdfRenderScheduler`` deliberately rasterises pages *without* PDFium's
annotation layer (see the comment there), so this module owns the on-page
appearance instead. That buys three things PDFium does not give us:

* replies (``/IRT``) are never drawn on the page — Acrobat shows a reply only
  in the comment list, not as a second icon stacked on its parent;
* a sticky-note icon keeps a **fixed pixel size** at every zoom level, which is
  what ``/NoZoom`` means and what a reader expects;
* the app can flash one annotation to answer a click in the sidebar.

Everything here is drawn from geometry in PDF page points; the caller supplies
a ``to_screen(x, y, w, h) -> QRectF`` mapper (``PdfView._page_rect_to_screen``)
so the same projection as the app's own highlights is used.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF

# A sticky-note icon is /NoZoom in practice: constant on screen, regardless of
# the zoom factor. 18 logical px reads clearly without covering the text line.
ICON_PX = 18.0
# The bubble pinned beside a commented highlight/box. Same idea, one size down
# so it reads as an attached marker rather than an annotation of its own.
MARKER_PX = 16.0
_DEFAULT_COLOR = "#ffd54f"
_MIN_STROKE_PX = 1.2


def _color(entry, fallback: str = _DEFAULT_COLOR) -> QColor:
    color = QColor(entry.color or fallback)
    if not color.isValid():
        color = QColor(fallback)
    return color


def _alpha(entry, base: int) -> int:
    opacity = getattr(entry, "opacity", 1.0)
    try:
        opacity = float(opacity)
    except (TypeError, ValueError):
        opacity = 1.0
    return max(12, min(255, int(round(base * max(0.0, min(1.0, opacity))))))


def visible_annotations(entries, page: int):
    """Annotations of *page* that belong on the page raster.

    Replies are excluded on purpose: Acrobat lists them under their parent in
    the comment pane and draws nothing extra on the page, and a reply's ``Rect``
    is usually pinned right on top of its parent, so drawing it would stack a
    second icon over the marked text.
    """
    return [
        e
        for e in (entries or [])
        if e.page == page and getattr(e, "in_reply_to", None) is None
    ]


def icon_rect(entry, to_screen) -> QRectF:
    """Fixed-size on-screen box for a sticky-note icon, anchored top-left."""
    x, y, w, h = entry.rect
    anchor = to_screen(x, y, w, h)
    return QRectF(anchor.left(), anchor.top(), ICON_PX, ICON_PX)


# Markup kinds that mark up the page itself rather than being an icon: a
# comment attached to one of these is otherwise invisible on the page.
_MARKER_KINDS = {
    "Highlight",
    "Underline",
    "StrikeOut",
    "Squiggly",
    "Square",
    "Circle",
    "Ink",
    "Line",
    "Polygon",
    "PolyLine",
    "FreeText",
}


def replies_by_parent(entries) -> dict:
    """Map a parent annotation's xref to the replies that answer it."""
    known = {e.xref for e in (entries or []) if e.xref}
    grouped: dict[int, list] = {}
    for entry in entries or []:
        parent = getattr(entry, "in_reply_to", None)
        if parent is not None and parent in known:
            grouped.setdefault(parent, []).append(entry)
    return grouped


def has_comment(entry, replies=()) -> bool:
    """True when the annotation carries text of its own or has replies."""
    return bool(getattr(entry, "note_text", "") or replies)


def wants_marker(entry, replies=()) -> bool:
    """Should a comment bubble be drawn beside this annotation?

    A bare highlight with nothing written on it needs no marker — the colour
    already says everything it has to say. One that carries a note, or that
    somebody replied to, otherwise looks identical to it, which is exactly the
    "where did the comment go?" problem this marker solves.
    """
    return entry.kind in _MARKER_KINDS and has_comment(entry, replies)


def marker_rect(entry, to_screen) -> QRectF:
    """Fixed-size bubble box just outside the annotation's top-right corner.

    Placed outside the geometry on purpose: sitting on top of the marked words
    would hide the very text the comment is about.
    """
    x, y, w, h = entry.rect
    box = to_screen(x, y, w, h)
    return QRectF(box.right() + 2.0, box.top() - MARKER_PX * 0.55, MARKER_PX, MARKER_PX)


def hit_rects(entry, to_screen, replies=()) -> list[QRectF]:
    """On-screen boxes that count as "the mouse is over this annotation"."""
    if entry.kind == "Text":
        return [icon_rect(entry, to_screen)]
    boxes: list[QRectF] = []
    if entry.quads:
        boxes = [to_screen(*q) for q in entry.quads]
    else:
        x, y, w, h = entry.rect
        if w > 0 or h > 0:
            boxes = [to_screen(x, y, w, h)]
    if wants_marker(entry, replies):
        boxes.append(marker_rect(entry, to_screen))
    return boxes


def _stroke_pen(color: QColor, width: float, alpha: int) -> QPen:
    pen_color = QColor(color)
    pen_color.setAlpha(alpha)
    pen = QPen(pen_color)
    pen.setWidthF(max(_MIN_STROKE_PX, width))
    pen.setCosmetic(False)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    return pen


def _screen_points(points, to_screen) -> list[QPointF]:
    out = []
    for x, y in points:
        r = to_screen(x, y, 0.0, 0.0)
        out.append(QPointF(r.left(), r.top()))
    return out


def _paint_text_markup(painter: QPainter, entry, to_screen, scale: float) -> None:
    color = _color(entry, "#ffd54f")
    boxes = [to_screen(*q) for q in entry.quads]
    if not boxes:
        x, y, w, h = entry.rect
        if w <= 0 or h <= 0:
            return
        boxes = [to_screen(x, y, w, h)]
    kind = entry.kind
    if kind == "Highlight":
        fill = QColor(color)
        fill.setAlpha(_alpha(entry, 235))
        # Multiply keeps the glyphs underneath legible, the way a real marker
        # pen (and Acrobat's own highlight appearance) behaves.
        painter.save()
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Multiply)
        for box in boxes:
            painter.fillRect(box, fill)
        painter.restore()
        return

    alpha = _alpha(entry, 235)
    for box in boxes:
        width = max(_MIN_STROKE_PX, box.height() * 0.07)
        painter.setPen(_stroke_pen(color, width, alpha))
        if kind == "StrikeOut":
            y = box.center().y()
            painter.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))
        elif kind == "Squiggly":
            _draw_squiggle(painter, box, scale)
        else:  # Underline
            y = box.bottom() - width
            painter.drawLine(QPointF(box.left(), y), QPointF(box.right(), y))


def _draw_squiggle(painter: QPainter, box: QRectF, scale: float) -> None:
    amplitude = max(1.0, box.height() * 0.09)
    period = max(3.0, 4.0 * max(0.5, scale))
    y = box.bottom() - amplitude
    points: list[QPointF] = []
    x = box.left()
    up = True
    while x < box.right():
        points.append(QPointF(x, y - amplitude if up else y + amplitude))
        up = not up
        x += period / 2.0
    points.append(QPointF(box.right(), y))
    if len(points) >= 2:
        painter.drawPolyline(QPolygonF(points))


def _paint_shape(painter: QPainter, entry, to_screen, scale: float) -> None:
    color = _color(entry, "#e53935")
    alpha = _alpha(entry, 255)
    width = max(_MIN_STROKE_PX, 1.5 * max(0.5, scale))
    painter.setPen(_stroke_pen(color, width, alpha))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    kind = entry.kind
    if kind == "Ink":
        for stroke in entry.ink:
            pts = _screen_points(stroke, to_screen)
            if len(pts) >= 2:
                painter.drawPolyline(QPolygonF(pts))
        return
    if kind in {"Line", "PolyLine", "Polygon"}:
        pts = _screen_points(entry.vertices, to_screen)
        if len(pts) < 2:
            return
        if kind == "Polygon":
            painter.drawPolygon(QPolygonF(pts))
        else:
            painter.drawPolyline(QPolygonF(pts))
        return
    x, y, w, h = entry.rect
    if w <= 0 or h <= 0:
        return
    box = to_screen(x, y, w, h).adjusted(width / 2, width / 2, -width / 2, -width / 2)
    if kind == "Circle":
        painter.drawEllipse(box)
    else:  # Square
        painter.drawRect(box)


def _paint_free_text(painter: QPainter, entry, to_screen, scale: float, theme_text) -> None:
    x, y, w, h = entry.rect
    if w <= 0 or h <= 0:
        return
    box = to_screen(x, y, w, h)
    color = _color(entry, "#5c6bc0")
    alpha = _alpha(entry, 255)
    painter.setPen(_stroke_pen(color, max(_MIN_STROKE_PX, 1.2 * max(0.5, scale)), alpha))
    background = QColor(255, 255, 255, 210)
    painter.setBrush(background)
    painter.drawRect(box)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    text = (entry.content or "").strip()
    if not text or box.height() < 8:
        return
    painter.save()
    font = painter.font()
    font.setPointSizeF(max(6.0, 9.0 * max(0.5, scale)))
    painter.setFont(font)
    painter.setPen(QColor(theme_text))
    painter.drawText(
        box.adjusted(3, 2, -3, -2),
        int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        | int(Qt.TextFlag.TextWordWrap),
        text,
    )
    painter.restore()


def _paint_sticky_icon(painter: QPainter, entry, to_screen) -> None:
    """A small speech-bubble marker of constant on-screen size."""
    _paint_bubble(painter, icon_rect(entry, to_screen), _color(entry, "#ffb300"),
                  _alpha(entry, 235))


def _paint_comment_marker(painter: QPainter, entry, to_screen) -> None:
    """The bubble pinned beside a highlight/box that carries a comment."""
    color = _color(entry, "#ffb300")
    # The marker must stay legible even when the markup itself is a 40%-opaque
    # highlight, so it does not inherit /CA.
    _paint_bubble(painter, marker_rect(entry, to_screen), color, 255)


def _paint_bubble(painter: QPainter, box: QRectF, color: QColor, alpha: int) -> None:
    fill = QColor(color)
    fill.setAlpha(alpha)
    body = QRectF(box.left(), box.top(), box.width(), box.height() * 0.78)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    border = QColor(color).darker(150)
    border.setAlpha(fill.alpha())
    pen = QPen(border)
    pen.setWidthF(1.0)
    painter.setPen(pen)
    painter.setBrush(fill)
    painter.drawRoundedRect(body, 3.0, 3.0)
    tail = QPolygonF(
        [
            QPointF(body.left() + body.width() * 0.22, body.bottom() - 0.5),
            QPointF(body.left() + body.width() * 0.22, box.bottom()),
            QPointF(body.left() + body.width() * 0.55, body.bottom() - 0.5),
        ]
    )
    painter.drawPolygon(tail)
    # Two short "lines of text" so the marker reads as a comment, not a blob.
    line_color = QColor(255, 255, 255, 230)
    if fill.lightnessF() > 0.7:
        line_color = QColor(60, 60, 60, 220)
    painter.setPen(QPen(line_color, 1.0))
    for i in (0.34, 0.58):
        ly = body.top() + body.height() * i
        painter.drawLine(
            QPointF(body.left() + 3.5, ly), QPointF(body.right() - 3.5, ly)
        )
    painter.restore()


def paint_embedded_annotations(
    painter: QPainter,
    entries,
    page: int,
    to_screen,
    scale: float = 1.0,
    *,
    flash_xref: int | None = None,
    theme_text: str = "#1f2328",
) -> int:
    """Draw every embedded annotation of *page*; return how many were drawn."""
    drawn = 0
    replies = replies_by_parent(entries)
    for entry in visible_annotations(entries, page):
        painter.save()
        try:
            kind = entry.kind
            if kind in {"Highlight", "Underline", "StrikeOut", "Squiggly"}:
                _paint_text_markup(painter, entry, to_screen, scale)
            elif kind in {"Square", "Circle", "Line", "Polygon", "PolyLine", "Ink"}:
                _paint_shape(painter, entry, to_screen, scale)
            elif kind == "FreeText":
                _paint_free_text(painter, entry, to_screen, scale, theme_text)
            elif kind == "Text":
                _paint_sticky_icon(painter, entry, to_screen)
            else:
                painter.restore()
                continue
            if wants_marker(entry, replies.get(entry.xref, ())):
                _paint_comment_marker(painter, entry, to_screen)
            drawn += 1
        finally:
            if painter.isActive():
                painter.restore()
        if flash_xref is not None and entry.xref == flash_xref:
            _paint_flash(painter, entry, to_screen)
    return drawn


def _paint_flash(painter: QPainter, entry, to_screen) -> None:
    """A short-lived focus ring drawn after a sidebar click."""
    boxes = hit_rects(entry, to_screen)[:1]
    if not boxes:
        return
    painter.save()
    pen = QPen(QColor("#1e88e5"))
    pen.setWidthF(2.0)
    painter.setPen(pen)
    painter.setBrush(QColor(30, 136, 229, 45))
    for box in boxes:
        painter.drawRoundedRect(box.adjusted(-3, -3, 3, 3), 3.0, 3.0)
    painter.restore()


def annotation_at(entries, page: int, pos, to_screen):
    """Topmost annotation whose on-screen geometry contains viewport *pos*."""
    replies = replies_by_parent(entries)
    point = QPointF(pos)
    for entry in reversed(visible_annotations(entries, page)):
        for box in hit_rects(entry, to_screen, replies.get(entry.xref, ())):
            if box.contains(point):
                return entry
    return None


def card_anchor(entry, to_screen, replies=()) -> QRectF:
    """The on-screen box a popup card should be pinned next to."""
    if wants_marker(entry, replies):
        return marker_rect(entry, to_screen)
    boxes = hit_rects(entry, to_screen, replies)
    return boxes[0] if boxes else QRectF()
