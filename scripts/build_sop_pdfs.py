"""
Build the SOP PDFs from their Markdown sources.

The Markdown files in data/sop_sources/ are the editable originals. This script
renders each one to a formatted PDF in data/sops/, which is what gets uploaded
to S3 and ingested into Pinecone.

Usage:
    python scripts/build_sop_pdfs.py
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

# __file__ is 'scripts/build_sop_pdfs.py'
PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = PROJECT_ROOT / "data" / "sop_sources"
OUTPUT_DIR = PROJECT_ROOT / "data" / "sops"

logger = logging.getLogger("sop_pdf_builder")


class SOPPdfBuilder:
    """Renders the small Markdown subset used in the SOPs to PDF."""

    def __init__(self) -> None:
        base = getSampleStyleSheet()
        # Title and section headings must be clearly larger than body text:
        # the PDF parser uses font size to find them again during ingestion.
        self.styles = {
            "title": ParagraphStyle("SOPTitle", parent=base["Title"], fontSize=16, leading=20,
                                    alignment=TA_LEFT, spaceAfter=8),
            "heading": ParagraphStyle("SOPHeading", parent=base["Heading2"], fontSize=13, leading=16,
                                      spaceBefore=10, spaceAfter=4),
            "body": ParagraphStyle("SOPBody", parent=base["BodyText"], fontSize=10, leading=14),
            "cell": ParagraphStyle("SOPCell", parent=base["BodyText"], fontSize=9, leading=12),
        }

    def build(self, source: Path, output: Path) -> None:
        """Render one Markdown SOP file to a PDF file."""
        lines = source.read_text(encoding="utf-8").splitlines()
        title = lines[0].lstrip("# ").strip() if lines else source.stem

        doc = SimpleDocTemplate(
            str(output), pagesize=A4, title=title, author="Cold Chain Operations",
            leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        )
        doc.build(self._to_flowables(lines))

    def _to_flowables(self, lines: list[str]) -> list:
        flowables = []
        table_rows: list[list[str]] = []

        for raw in lines + [""]:  # trailing blank line flushes a final table
            line = raw.strip()

            if line.startswith("|"):
                cells = [c.strip() for c in line.strip("|").split("|")]
                if not all(re.fullmatch(r":?-+:?", c) for c in cells):  # skip the |---| separator row
                    table_rows.append(cells)
                continue
            if table_rows:
                flowables += [self._table(table_rows), Spacer(1, 6)]
                table_rows = []

            if not line:
                continue
            if line.startswith("## "):
                flowables.append(Paragraph(self._inline(line[3:]), self.styles["heading"]))
            elif line.startswith("# "):
                flowables.append(Paragraph(self._inline(line[2:]), self.styles["title"]))
            elif line.startswith("- "):
                flowables.append(Paragraph(self._inline(line[2:]), self.styles["body"], bulletText="•"))
            elif m := re.match(r"(\d+)\.\s+(.*)", line):
                flowables.append(Paragraph(self._inline(m.group(2)), self.styles["body"],
                                           bulletText=f"{m.group(1)}."))
            else:
                flowables.append(Paragraph(self._inline(line), self.styles["body"]))

        return flowables

    def _table(self, rows: list[list[str]]) -> Table:
        data = [[Paragraph(self._inline(c), self.styles["cell"]) for c in row] for row in rows]
        table = Table(data, repeatRows=1, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#E8EEF5")),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9AA5B1")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        return table

    @staticmethod
    def _inline(text: str) -> str:
        """Convert **bold** and `code` to ReportLab markup (escaping XML characters first)."""
        text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
        return re.sub(r"`(.+?)`", r'<font name="Courier">\1</font>', text)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

    sources = sorted(SOURCE_DIR.glob("*.md"))
    if not sources:
        logger.error("No .md files found in %s", SOURCE_DIR)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    builder = SOPPdfBuilder()
    for source in sources:
        output = OUTPUT_DIR / f"{source.stem}.pdf"
        builder.build(source, output)
        logger.info("  %s -> %s", source.name, output.relative_to(PROJECT_ROOT))

    logger.info("✅ Built %d SOP PDFs in %s", len(sources), OUTPUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
