"""Render the Meridian documents in seed/meridian/content.py to text PDFs.

    .venv/bin/python scripts/make_seed_pdfs.py      # writes seed/meridian/pdf/ and seed/meridian/upload/
"""

from __future__ import annotations

import pathlib
import re
import sys

from reportlab.lib import colors
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import ListFlowable, ListItem, Paragraph, SimpleDocTemplate, Spacer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from seed.meridian.content import DOCUMENTS, UPLOAD_SAMPLES  # noqa: E402

INK, BRAND = colors.HexColor("#181626"), colors.HexColor("#6246EA")
base = getSampleStyleSheet()
H1 = ParagraphStyle("h1", parent=base["Title"], fontName="Helvetica-Bold", fontSize=20, leading=24, textColor=INK, alignment=0, spaceAfter=6)
H2 = ParagraphStyle("h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=BRAND, spaceBefore=10, spaceAfter=4)
BODY = ParagraphStyle("body", parent=base["BodyText"], fontName="Helvetica", fontSize=10.5, leading=15, textColor=INK, spaceAfter=6)


def inline(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


def render(body: str, out: pathlib.Path, title: str) -> None:
    story, items, ordered = [], [], False

    def flush():
        nonlocal items
        if items:
            story.append(ListFlowable([ListItem(Paragraph(inline(t), BODY), leftIndent=14) for t in items],
                                      bulletType="1" if ordered else "bullet", start="1" if ordered else None,
                                      leftIndent=14, bulletFontSize=9, bulletColor=BRAND))
            items = []

    for line in body.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\.\s+(.*)", line)
        if line.startswith("- ") or m:
            if items and ordered != bool(m):
                flush()
            ordered = bool(m)
            items.append(m.group(2) if m else line[2:])
            continue
        flush()
        if line.startswith("## "):
            story.append(Paragraph(inline(line[3:]), H2))
        elif line.startswith("# "):
            story += [Paragraph(inline(line[2:]), H1), Spacer(1, 2)]
        else:
            story.append(Paragraph(inline(line), BODY))
    flush()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#68647D"))
        canvas.drawString(inch, 0.6 * inch, f"Meridian  ·  {title}")
        canvas.drawRightString(LETTER[0] - inch, 0.6 * inch, f"Page {doc.page}")
        canvas.restoreState()

    out.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(str(out), pagesize=LETTER, title=title, author="Meridian",
                      leftMargin=inch, rightMargin=inch, topMargin=0.9 * inch, bottomMargin=0.9 * inch
                      ).build(story, onFirstPage=footer, onLaterPages=footer)


def main() -> None:
    for stem, title, *_rest, body in DOCUMENTS:
        render(body, ROOT / "seed/meridian/pdf" / f"{title.replace(':', ' -')}.pdf", title)
    for stem, title, body in UPLOAD_SAMPLES:
        render(body, ROOT / "seed/meridian/upload" / f"{title}.pdf", title)
    print(f"rendered {len(DOCUMENTS)} documents and {len(UPLOAD_SAMPLES)} upload samples")


if __name__ == "__main__":
    main()
