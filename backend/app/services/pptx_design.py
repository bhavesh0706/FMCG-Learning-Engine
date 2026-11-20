"""
Design system for the PowerPoint reports (services/custom_reports.build_pptx).

A 16:9 deck in the application's brand: navy, deep navy and a gold accent, white cards on
a soft background, styled charts (no grid lines, data labels), an insight callout, and a
footer with the slide number. Only python-pptx shapes are used, so every slide stays
editable in PowerPoint, Keynote and Google Slides.
"""

from __future__ import annotations

from datetime import datetime

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

# ---- palette (the application's colours) ----
DEEP = RGBColor(0x00, 0x1D, 0x39)
NAVY = RGBColor(0x0A, 0x41, 0x74)
NAVY_2 = RGBColor(0x12, 0x55, 0x93)
SKY = RGBColor(0x3D, 0x8B, 0xD8)
GOLD = RGBColor(0xF2, 0xB3, 0x3D)
INK = RGBColor(0x1B, 0x2A, 0x3A)
MUTED = RGBColor(0x5B, 0x6B, 0x7C)
LINE = RGBColor(0xDD, 0xE5, 0xEE)
BG = RGBColor(0xF4, 0xF7, 0xFB)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)
POS, POS_SOFT = RGBColor(0x1E, 0x8A, 0x5A), RGBColor(0xE3, 0xF4, 0xEC)
MIX, MIX_SOFT = RGBColor(0xC9, 0x8A, 0x1B), RGBColor(0xFB, 0xF1, 0xDE)
NEG, NEG_SOFT = RGBColor(0xC8, 0x41, 0x4F), RGBColor(0xFB, 0xE9, 0xEB)
FONT = "Calibri"
W, H = 13.333, 7.5  # inches


class Deck:
    """A presentation with the brand frame; each method adds or decorates one slide."""

    def __init__(self, brand: str = "FMCG Video Learning Engine", footer: str = ""):
        self.prs = Presentation()
        self.prs.slide_width, self.prs.slide_height = Inches(W), Inches(H)
        self.blank = self.prs.slide_layouts[6]
        self.brand, self.footer = brand, footer
        self.count = 0

    # ---------------------------------------------------------------- primitives
    @staticmethod
    def box(slide, x, y, w, h, fill=None, line=None, shape=MSO_SHAPE.RECTANGLE, radius=None, line_w=0.75):
        s = slide.shapes.add_shape(shape, Inches(x), Inches(y), Inches(w), Inches(h))
        if fill is None:
            s.fill.background()
        else:
            s.fill.solid()
            s.fill.fore_color.rgb = fill
        if line is None:
            s.line.fill.background()
        else:
            s.line.color.rgb = line
            s.line.width = Pt(line_w)
        if radius is not None and shape == MSO_SHAPE.ROUNDED_RECTANGLE:
            s.adjustments[0] = radius
        s.shadow.inherit = False
        s.text_frame.text = ""
        return s

    @staticmethod
    def text(slide, x, y, w, h, value, size=14, bold=False, color=INK, align=PP_ALIGN.LEFT, italic=False,
             anchor=MSO_ANCHOR.TOP, spacing=None):
        tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
        tf = tb.text_frame
        tf.word_wrap = True
        tf.margin_left = tf.margin_right = Inches(0.02)
        tf.margin_top = tf.margin_bottom = Inches(0.01)
        tf.vertical_anchor = anchor
        lines = value if isinstance(value, list) else [value]
        for i, line in enumerate(lines):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.alignment = align
            if spacing:
                p.space_after = Pt(spacing)
            r = p.add_run()
            r.text = str(line)
            r.font.size, r.font.bold, r.font.italic, r.font.name = Pt(size), bold, italic, FONT
            r.font.color.rgb = color
        return tb

    def logo(self, slide, x, y, size=0.5, on_dark=True):
        """The application mark: a speech bubble with a play button and a gold spark."""
        k = size / 1024
        self.box(slide, x, y, size, size, fill=NAVY_2 if on_dark else NAVY, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.22)
        self.box(slide, x + 232 * k, y + 248 * k, 560 * k, 440 * k, fill=WHITE, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.27)
        tail = self.box(slide, x + 300 * k, y + 640 * k, 150 * k, 160 * k, fill=WHITE, shape=MSO_SHAPE.RIGHT_TRIANGLE)
        tail.rotation = 180
        play = self.box(slide, x + 430 * k, y + 360 * k, 200 * k, 216 * k, fill=NAVY, shape=MSO_SHAPE.ISOSCELES_TRIANGLE)
        play.rotation = 90
        self.box(slide, x + 716 * k, y + 218 * k, 88 * k, 88 * k, fill=GOLD, shape=MSO_SHAPE.OVAL)

    # ---------------------------------------------------------------- frames
    def cover(self, title: str, subtitle: str, stats: list):
        """Dark cover: brand, title, what the report covers, three headline numbers."""
        s = self.prs.slides.add_slide(self.blank)
        self.count += 1
        self.box(s, 0, 0, W, H, fill=DEEP)
        self.box(s, 8.6, -2.4, 7.4, 7.4, fill=NAVY, shape=MSO_SHAPE.OVAL)
        self.box(s, 10.9, 4.9, 3.6, 3.6, fill=NAVY_2, shape=MSO_SHAPE.OVAL)
        self.box(s, 11.75, 1.15, 0.55, 0.55, fill=GOLD, shape=MSO_SHAPE.OVAL)
        self.box(s, 0.8, 2.35, 0.09, 1.75, fill=GOLD)
        self.logo(s, 0.8, 0.65, 0.5)
        self.text(s, 1.45, 0.72, 6, 0.4, self.brand, 14, True, WHITE)
        self.text(s, 1.1, 2.25, 8.4, 0.4, "CONSUMER INSIGHTS REPORT", 12, True, GOLD)
        self.text(s, 1.1, 2.6, 8.6, 1.3, title, 38, True, WHITE, anchor=MSO_ANCHOR.TOP)
        self.text(s, 1.1, 3.75, 8.2, 0.9, subtitle, 13, False, RGBColor(0xB9, 0xC9, 0xDA))
        x = 1.1
        for label, value in stats[:3]:
            card = self.box(s, x, 5.25, 2.55, 1.2, fill=NAVY, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.12)
            card.line.color.rgb = NAVY_2
            self.text(s, x + 0.22, 5.38, 2.2, 0.55, value, 26, True, WHITE)
            self.text(s, x + 0.22, 5.92, 2.2, 0.4, label, 11, False, RGBColor(0xB9, 0xC9, 0xDA))
            x += 2.75
        self.text(s, 1.1, 6.85, 6, 0.3, datetime.utcnow().strftime("%d %B %Y"), 11, False, RGBColor(0x8F, 0xA6, 0xBE))
        return s

    def divider(self, kicker: str, title: str, subtitle: str = ""):
        """A section opener for one product (when the deck covers several)."""
        s = self.prs.slides.add_slide(self.blank)
        self.count += 1
        self.box(s, 0, 0, W, H, fill=NAVY)
        self.box(s, -1.6, 3.2, 6.2, 6.2, fill=NAVY_2, shape=MSO_SHAPE.OVAL)
        self.box(s, 1.0, 2.75, 0.09, 1.6, fill=GOLD)
        self.text(s, 1.3, 2.65, 10, 0.4, kicker.upper(), 12, True, GOLD)
        self.text(s, 1.3, 3.0, 10.5, 1.0, title, 40, True, WHITE)
        if subtitle:
            self.text(s, 1.3, 3.95, 10.5, 0.5, subtitle, 15, False, RGBColor(0xC9, 0xD6, 0xE4))
        self.text(s, W - 1.3, H - 0.55, 0.8, 0.3, str(self.count), 10, False, RGBColor(0xC9, 0xD6, 0xE4), PP_ALIGN.RIGHT)
        return s

    def slide(self, kicker: str, title: str, subtitle: str = "", chip: str = ""):
        """A content slide: soft background, kicker, title, optional product chip, footer."""
        s = self.prs.slides.add_slide(self.blank)
        self.count += 1
        self.box(s, 0, 0, W, H, fill=BG)
        self.box(s, 0, 0, W, 0.09, fill=NAVY)
        self.box(s, 0.6, 0.55, 0.42, 0.06, fill=GOLD)
        self.text(s, 0.6, 0.66, 9, 0.3, kicker.upper(), 11, True, NAVY)
        self.text(s, 0.6, 0.92, 9.8, 0.7, title, 26, True, DEEP)
        if subtitle:
            self.text(s, 0.6, 1.52, 10, 0.35, subtitle, 12, False, MUTED)
        if chip:
            w = min(3.6, 0.35 + 0.085 * len(chip))
            c = self.box(s, W - 0.6 - w, 0.66, w, 0.38, fill=WHITE, line=LINE, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.5)
            c.text_frame.text = chip
            self._style_shape_text(c, 10.5, True, NAVY, PP_ALIGN.CENTER)
        self.box(s, 0.6, H - 0.48, W - 1.2, 0.012, fill=LINE)
        self.text(s, 0.6, H - 0.42, 8, 0.3, self.footer or self.brand, 9, False, MUTED)
        self.text(s, W - 1.4, H - 0.42, 0.8, 0.3, str(self.count), 9, True, NAVY, PP_ALIGN.RIGHT)
        return s

    @staticmethod
    def _style_shape_text(shape, size, bold, color, align=PP_ALIGN.LEFT):
        tf = shape.text_frame
        tf.margin_left = tf.margin_right = Inches(0.06)
        tf.vertical_anchor = MSO_ANCHOR.MIDDLE
        for p in tf.paragraphs:
            p.alignment = align
            for r in p.runs:
                r.font.size, r.font.bold, r.font.name = Pt(size), bold, FONT
                r.font.color.rgb = color

    # ---------------------------------------------------------------- components
    def kpi(self, slide, x, y, w, h, value, label, accent=NAVY, note=""):
        self.box(slide, x, y, w, h, fill=WHITE, line=LINE, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.08)
        self.box(slide, x, y + 0.18, 0.07, h - 0.36, fill=accent)
        self.text(slide, x + 0.3, y + 0.16, w - 0.45, 0.32, label.upper(), 10, True, MUTED)
        self.text(slide, x + 0.3, y + 0.46, w - 0.45, 0.7, value, 30, True, DEEP)
        if note:
            self.text(slide, x + 0.3, y + h - 0.45, w - 0.45, 0.3, note, 10.5, False, accent)

    def callout(self, slide, x, y, w, h, label, value):
        """An insight box with a gold bar: the one sentence to remember."""
        self.box(slide, x, y, w, h, fill=WHITE, line=LINE, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.06)
        self.box(slide, x, y, 0.09, h, fill=GOLD)
        self.text(slide, x + 0.3, y + 0.14, w - 0.5, 0.3, label.upper(), 10, True, MIX)
        self.text(slide, x + 0.3, y + 0.42, w - 0.5, h - 0.5, value, 13, False, INK)

    def meter_card(self, slide, x, y, w, name, count, total, quote, tone="pos"):
        """One finding: name, 'N of M customers', a bar, and the customer's words."""
        color, soft = (POS, POS_SOFT) if tone == "pos" else (NEG, NEG_SOFT)
        h = 0.98 if quote else 0.66
        self.box(slide, x, y, w, h, fill=WHITE, line=LINE, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.08)
        self.box(slide, x, y + 0.12, 0.07, h - 0.24, fill=color)
        self.text(slide, x + 0.28, y + 0.1, w - 2.5, 0.32, name, 13.5, True, DEEP)
        self.text(slide, x + w - 2.3, y + 0.1, 2.1, 0.32, f"{count} of {total} customers", 11, True, color, PP_ALIGN.RIGHT)
        bar_w = w - 0.56
        self.box(slide, x + 0.28, y + 0.47, bar_w, 0.09, fill=soft, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.5)
        share = max(0.03, min(1.0, count / total)) if total else 0.03
        self.box(slide, x + 0.28, y + 0.47, bar_w * share, 0.09, fill=color, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.5)
        if quote:
            self.text(slide, x + 0.28, y + 0.62, w - 0.5, 0.32, f"“{quote[:110]}”", 10.5, False, MUTED, italic=True)
        return h

    def numbered(self, slide, x, y, w, n, text, sub=""):
        """A numbered action card."""
        h = 0.95 if sub else 0.78
        self.box(slide, x, y, w, h, fill=WHITE, line=LINE, shape=MSO_SHAPE.ROUNDED_RECTANGLE, radius=0.08)
        c = self.box(slide, x + 0.22, y + (h - 0.48) / 2, 0.48, 0.48, fill=GOLD, shape=MSO_SHAPE.OVAL)
        c.text_frame.text = str(n)
        self._style_shape_text(c, 14, True, DEEP, PP_ALIGN.CENTER)
        self.text(slide, x + 0.9, y + 0.14, w - 1.1, 0.5, text[:150], 13, True, DEEP)
        if sub:
            self.text(slide, x + 0.9, y + 0.55, w - 1.1, 0.35, sub[:140], 10.5, False, MUTED)
        return h

    def table(self, slide, x, y, w, headers, rows, col_w=None, max_rows=10):
        rows = rows[:max_rows]
        shape = slide.shapes.add_table(len(rows) + 1, len(headers), Inches(x), Inches(y), Inches(w), Inches(0.42 * (len(rows) + 1)))
        t = shape.table
        if col_w:
            for i, cw in enumerate(col_w):
                t.columns[i].width = Inches(cw)
        for r in range(len(rows) + 1):
            t.rows[r].height = Inches(0.42)
            for c in range(len(headers)):
                cell = t.cell(r, c)
                cell.text = str(headers[c]) if r == 0 else ("–" if rows[r - 1][c] is None else str(rows[r - 1][c]))
                cell.fill.solid()
                cell.fill.fore_color.rgb = NAVY if r == 0 else (WHITE if r % 2 else BG)
                cell.margin_left = cell.margin_right = Inches(0.1)
                cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                for p in cell.text_frame.paragraphs:
                    p.alignment = PP_ALIGN.LEFT if c == 0 else PP_ALIGN.CENTER
                    for run in p.runs:
                        run.font.size, run.font.name = Pt(11 if r else 10.5), FONT
                        run.font.bold = r == 0 or c == 0
                        run.font.color.rgb = WHITE if r == 0 else INK
        return shape

    # ---------------------------------------------------------------- charts
    @staticmethod
    def _chart_text(chart, size=11):
        chart.has_title = False  # the slide title says what the chart shows
        chart.font.size, chart.font.name = Pt(size), FONT
        chart.font.color.rgb = MUTED

    def bar_chart(self, slide, x, y, w, h, categories, values, horizontal=False, color=NAVY, number_format='0.0',
                  max_value=None, colors=None):
        data = CategoryChartData()
        data.categories = [str(c)[:34] for c in categories]
        data.add_series("Value", [v if v is not None else 0 for v in values])
        kind = XL_CHART_TYPE.BAR_CLUSTERED if horizontal else XL_CHART_TYPE.COLUMN_CLUSTERED
        chart = slide.shapes.add_chart(kind, Inches(x), Inches(y), Inches(w), Inches(h), data).chart
        self._chart_text(chart)
        chart.has_legend = False
        plot = chart.plots[0]
        plot.gap_width = 70
        plot.has_data_labels = True
        dl = plot.data_labels
        dl.number_format, dl.number_format_is_linked = number_format, False
        dl.font.size, dl.font.bold, dl.font.color.rgb = Pt(11), True, DEEP
        dl.position = XL_LABEL_POSITION.OUTSIDE_END
        series = plot.series[0]
        series.format.fill.solid()
        series.format.fill.fore_color.rgb = color
        if colors:
            for i, c in enumerate(colors):
                pt = series.points[i]
                pt.format.fill.solid()
                pt.format.fill.fore_color.rgb = c
        va = chart.value_axis
        va.has_major_gridlines = False
        va.visible = False
        if max_value:
            va.maximum_scale = max_value
        va.minimum_scale = 0
        ca = chart.category_axis
        ca.format.line.color.rgb = LINE
        ca.tick_labels.font.size = Pt(11)
        ca.tick_labels.font.color.rgb = INK
        if horizontal:
            ca.reverse_order = True
        return chart

    def donut(self, slide, x, y, w, h, labels, values, colors):
        data = CategoryChartData()
        data.categories = labels
        data.add_series("Share", values)
        chart = slide.shapes.add_chart(XL_CHART_TYPE.DOUGHNUT, Inches(x), Inches(y), Inches(w), Inches(h), data).chart
        self._chart_text(chart)
        chart.has_legend = True
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(11)
        plot = chart.plots[0]
        for i, c in enumerate(colors):
            pt = plot.series[0].points[i]
            pt.format.fill.solid()
            pt.format.fill.fore_color.rgb = c
        plot.has_data_labels = True
        plot.data_labels.number_format, plot.data_labels.number_format_is_linked = '0', False
        plot.data_labels.font.size, plot.data_labels.font.bold = Pt(12), True
        plot.data_labels.font.color.rgb = WHITE
        return chart

    def save(self, buf):
        self.prs.save(buf)


def stars(rating) -> str:
    if rating is None:
        return ""
    full = int(round(rating))
    return "★" * full + "☆" * (5 - full)


def pct(part, whole) -> str:
    return f"{round(100 * part / whole)}%" if whole else "–"


def emu(inches: float) -> Emu:
    return Inches(inches)