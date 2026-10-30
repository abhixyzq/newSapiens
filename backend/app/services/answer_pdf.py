import io
import re
from xml.sax.saxutils import escape


def render(title: str, question: str, msg: dict) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

    from .reports import _font
    f, fb, uni = _font()

    def t(x):
        x = escape(x or "")
        return x if uni else x.encode("latin-1", "replace").decode("latin-1")

    def md(x):
        x = t(x)
        x = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", x)
        x = re.sub(r"(?<![\w*])_(.+?)_(?![\w*])", r"<i>\1</i>", x)
        return re.sub(r"\[(S\d+)\]", r'<font color="#0EA5C6" size="7">[\1]</font>', x)

    base = ParagraphStyle("b", fontName=f, fontSize=10, leading=14)
    h = ParagraphStyle("h", fontName=fb, fontSize=15, leading=19, textColor=colors.HexColor("#0B1B3B"), spaceAfter=6)
    small = ParagraphStyle("s", fontName=f, fontSize=8, leading=11, textColor=colors.HexColor("#475569"))
    story = [Paragraph("NEW SAPIENS — Answer", h), Paragraph(f"<b>Question:</b> {t(question)}", base), Spacer(1, 8)]
    for line in msg["content"].split("\n"):
        if line.strip():
            story.append(Paragraph(md(line.lstrip("-• ").strip() if line.startswith(("- ", "• ")) else line) if not line.startswith(("- ", "• ")) else "• " + md(line[2:]), base))
    if msg["citations"]:
        story += [Spacer(1, 10), Paragraph("<b>Sources</b>", base)]
        for c in msg["citations"]:
            ts = c["start_ms"] or 0
            story.append(Paragraph(f'[{c["ref"]}] {t(c["label"])} @ {ts // 60000:02d}:{(ts // 1000) % 60:02d} — {t((c["snippet"] or "")[:220])}', small))
    story += [Spacer(1, 10), Paragraph("Generated from your recorded conversations. AI-detected items are unverified unless marked confirmed.", small)]
    buf = io.BytesIO()
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm, title=title).build(story)
    return buf.getvalue()
