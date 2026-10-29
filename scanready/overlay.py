from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .report import AnalysisReport, CheckResult, Region, Status

# Display styling only; detector and decision thresholds live in config/thresholds.yaml.
_STATUS_RGB = {Status.PASS: (46, 160, 67), Status.WARN: (230, 150, 0), Status.FAIL: (215, 40, 40)}
_PANEL_RGBA = (18, 18, 18, 200)
_TEXT_RGB = (255, 255, 255)
_WIDTH_PER_TEXT_PIXEL = 55
_MINIMUM_TEXT_PIXELS = 14


@dataclass(frozen=True)
class TextBox:
    kind: str
    text: str
    rect: tuple[int, int, int, int]
    status: Status | None = None


def evidence_lines(report: AnalysisReport) -> tuple[str, ...]:
    return tuple(f"{_measurement_text(check)} - {check.status.value}" for check in report.checks)


def region_primitive(region: Region) -> str:
    if region.kind == "skew_axis":
        return "line"
    if len(region.points) == 2:
        return "box"
    return "polygon"


def _measurement_text(check: CheckResult) -> str:
    return f"{check.name.replace('_', ' ').title()}: {check.measurement:.3g} {check.unit} (limit {check.limit:.3g})"


@lru_cache(maxsize=32)
def _font(size: int) -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, OSError):
        return ImageFont.load_default()


def _text_width(text: str, font: ImageFont.ImageFont) -> int:
    left, _, right, _ = font.getbbox(text)
    return int(np.ceil(right - left))


def _line_height(font: ImageFont.ImageFont) -> int:
    ascent, descent = font.getmetrics()
    return int(ascent + descent)


def _text_size(width: int) -> int:
    return max(_MINIMUM_TEXT_PIXELS, round(width / _WIDTH_PER_TEXT_PIXEL))


def _panel_layout(report: AnalysisReport, width: int, height: int) -> tuple[int, list[TextBox], TextBox]:
    size = _text_size(width)
    while True:
        font = _font(size)
        pad = max(4, size // 3)
        margin = max(4, size // 2)
        line_height = _line_height(font) + pad
        tag_width = _text_width("WARN", font) + 2 * pad
        text_width = max(_text_width(_measurement_text(check), font) for check in report.checks)
        panel_width = text_width + tag_width + 4 * pad
        panel_height = line_height * len(report.checks) + 2 * pad
        fits = panel_width + 2 * margin <= width and panel_height + 2 * margin <= height
        if fits or size <= 8:
            break
        size -= 1
    x0, y0 = margin, margin
    panel = TextBox("panel", "", (x0, y0, min(width, x0 + panel_width), min(height, y0 + panel_height)))
    lines = []
    for index, check in enumerate(report.checks):
        top = y0 + pad + index * line_height
        lines.append(TextBox("line", _measurement_text(check), (x0 + pad, top, panel.rect[2] - pad, top + line_height - 1), check.status))
    return size, lines, panel


def _label_candidates(region: Region, box_width: int, box_height: int, gap: int) -> list[tuple[int, int]]:
    xs = [point[0] for point in region.points]
    ys = [point[1] for point in region.points]
    left, top, right, bottom = min(xs), min(ys), max(xs), max(ys)
    if region_primitive(region) == "line":
        return [(left, top - box_height - gap), (left, bottom + gap), (right - box_width, top - box_height - gap)]
    return [
        (left + gap, top + gap),
        (right - box_width - gap, top + gap),
        (left + gap, bottom - box_height - gap),
        (right - box_width - gap, bottom - box_height - gap),
        (left, top - box_height - gap),
        (left, bottom + gap),
    ]


def _label_width(name: str, status: Status, font: ImageFont.ImageFont, pad: int) -> int:
    return _text_width(name, font) + _text_width(status.value, font) + 5 * pad


def _overlaps(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _clamp(x: int, y: int, box_width: int, box_height: int, width: int, height: int) -> tuple[int, int, int, int]:
    x = min(max(0, x), max(0, width - box_width))
    y = min(max(0, y), max(0, height - box_height))
    return x, y, min(width, x + box_width), min(height, y + box_height)


def evidence_layout(report: AnalysisReport, width: int, height: int) -> list[TextBox]:
    """Place the evidence panel and region labels so no text box overlaps another."""
    return _layout(report, width, height)[1]


def _layout(report: AnalysisReport, width: int, height: int) -> tuple[int, list[TextBox]]:
    size, lines, panel = _panel_layout(report, width, height)
    font = _font(size)
    pad = max(4, size // 3)
    box_height = _line_height(font) + pad
    occupied = [panel.rect]
    labels = []
    for check in report.checks:
        for region in check.regions:
            if not region.points:
                continue
            text = f"{region.label}: {check.status.value}"
            box_width = _label_width(region.label, check.status, font, pad)
            chosen = None
            for x, y in _label_candidates(region, box_width, box_height, pad):
                rect = _clamp(x, y, box_width, box_height, width, height)
                if not any(_overlaps(rect, other) for other in occupied):
                    chosen = rect
                    break
            if chosen is None:
                # Stack below everything already placed in the left column.
                y = max(other[3] for other in occupied) + pad
                chosen = _clamp(panel.rect[0], y, box_width, box_height, width, height)
            occupied.append(chosen)
            labels.append(TextBox("label", text, chosen, check.status))
    return size, [panel, *lines, *labels]


def _dashed_line(canvas: np.ndarray, start: tuple[int, int], end: tuple[int, int], color, thickness: int) -> None:
    start_v = np.array(start, dtype=float)
    end_v = np.array(end, dtype=float)
    distance = float(np.linalg.norm(end_v - start_v))
    if distance == 0:
        return
    segments = max(1, int(distance // 16))
    for index in range(0, segments, 2):
        a = start_v + (end_v - start_v) * (index / segments)
        b = start_v + (end_v - start_v) * (min(index + 1, segments) / segments)
        cv2.line(canvas, tuple(a.astype(int)), tuple(b.astype(int)), color, thickness, cv2.LINE_AA)


def _draw_region(canvas: np.ndarray, region: Region, color, *, dashed: bool, thickness: int) -> None:
    points = np.array(region.points, dtype=np.int32)
    primitive = region_primitive(region)
    if primitive == "line":
        cv2.line(canvas, tuple(points[0]), tuple(points[1]), color, thickness, cv2.LINE_AA)
    elif primitive == "box":
        x1, y1 = points[0]
        x2, y2 = points[1]
        if dashed:
            for start, end in [((x1, y1), (x2, y1)), ((x2, y1), (x2, y2)), ((x2, y2), (x1, y2)), ((x1, y2), (x1, y1))]:
                _dashed_line(canvas, start, end, color, thickness)
        else:
            cv2.rectangle(canvas, (x1, y1), (x2, y2), color, thickness)
    elif len(points) > 2:
        cv2.polylines(canvas, [points], True, color, thickness, cv2.LINE_AA)


def _bgr(status: Status) -> tuple[int, int, int]:
    red, green, blue = _STATUS_RGB[status]
    return blue, green, red


def draw_evidence(original: np.ndarray, report: AnalysisReport) -> np.ndarray:
    canvas = original.copy()
    height, width = canvas.shape[:2]
    thickness = max(2, width // 500)
    for check in report.checks:
        for region in check.regions:
            if region.points:
                _draw_region(canvas, region, _bgr(check.status), dashed=region.kind in {"shadow", "glare", "page_boundary"}, thickness=thickness)

    size, boxes = _layout(report, width, height)
    panel = next(box for box in boxes if box.kind == "panel")
    font = _font(size)
    pad = max(4, size // 3)

    image = Image.fromarray(cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)).convert("RGBA")
    layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    draw.rectangle(panel.rect, fill=_PANEL_RGBA)
    for box in boxes:
        if box.kind == "panel" or box.status is None:
            continue
        x0, y0, x1, y1 = box.rect
        if box.kind == "label":
            name = box.text.rpartition(": ")[0]
            tag = box.status.value
            tag_width = _text_width(tag, font) + 2 * pad
            draw.rectangle(box.rect, fill=_PANEL_RGBA)
            draw.text((x0 + pad, y0 + pad // 2), name, font=font, fill=_TEXT_RGB + (255,))
            tag_rect = (x1 - tag_width - pad // 2, y0 + 2, x1 - pad // 2, y1 - 2)
            draw.rectangle(tag_rect, fill=_STATUS_RGB[box.status] + (255,))
            draw.text((tag_rect[0] + pad, y0 + pad // 2), tag, font=font, fill=_TEXT_RGB + (255,))
        else:
            tag = box.status.value
            tag_width = _text_width(tag, font) + 2 * pad
            draw.text((x0, y0 + pad // 2), box.text, font=font, fill=_TEXT_RGB + (255,))
            tag_rect = (x1 - tag_width, y0 + 1, x1, y1 - 1)
            draw.rectangle(tag_rect, fill=_STATUS_RGB[box.status] + (255,))
            draw.text((tag_rect[0] + pad, y0 + pad // 2), tag, font=font, fill=_TEXT_RGB + (255,))
    composed = Image.alpha_composite(image, layer).convert("RGB")
    return cv2.cvtColor(np.asarray(composed), cv2.COLOR_RGB2BGR)
