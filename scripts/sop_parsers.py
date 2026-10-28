"""
Convert SOP files (PDF, Word .docx, Markdown) into Markdown text.

The ingestion pipeline splits SOPs on Markdown headings ('#' = SOP title,
'##' = section), so every parser's job is to recover those headings from its
file format:
  - PDF:  headings are detected by font size (larger than the body text).
  - DOCX: headings come from Word paragraph styles (Title, Heading 1, 2, ...).
  - MD:   already Markdown, used as is.
"""

from __future__ import annotations

import io
import re
from collections import Counter
from pathlib import PurePath

from docx import Document as DocxDocument
from docx.table import Table as DocxTable
from pypdf import PdfReader

from exception import UnsupportedFormatError
SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".md")

# A heading must be at least this much bigger than the body text (PDF only)
HEADING_SIZE_RATIO = 1.15
# Bold paragraphs shorter than this are treated as headings in Word files without heading styles
MAX_HEADING_LENGTH = 80
# Bullet characters as they come out of PDF text extraction (varies by PDF producer)
PDF_BULLETS = ("•", "▪", "◦", "", "\x7f", "\x95")




def to_markdown(name: str, data: bytes) -> str:
    """Convert one SOP file's bytes into Markdown text, based on its extension."""
    ext = PurePath(name).suffix.lower()
    if ext == ".pdf":
        return pdf_to_markdown(data)
    if ext == ".docx":
        return docx_to_markdown(data)
    if ext == ".md":
        return data.decode("utf-8")
    if ext == ".doc":
        raise UnsupportedFormatError(
            f"{name}: old Word .doc format is not supported. Open it in Word and save as .docx or PDF."
        )
    raise UnsupportedFormatError(f"{name}: unsupported file type '{ext}'.")


# ---------- PDF ----------

def pdf_to_markdown(data: bytes) -> str:
    """Extract PDF text and mark headings ('#', '##') using font sizes."""
    reader = PdfReader(io.BytesIO(data))

    # 1. Record every text fragment with its font size
    fragments: list[tuple[str, float]] = []

    def visitor(text, cm, tm, font_dict, font_size):
        text = text.strip()
        if text:
            # Effective size = font size scaled by the text matrix
            fragments.append((text, round(font_size * (abs(tm[3]) or 1), 1)))

    pages_text = [page.extract_text(visitor_text=visitor) for page in reader.pages]
    if not fragments:
        raise UnsupportedFormatError("PDF has no extractable text (it may be a scanned image).")

    # 2. Body size = the font size used for the most characters
    size_counts = Counter()
    for text, size in fragments:
        size_counts[size] += len(text)
    body_size = size_counts.most_common(1)[0][0]

    # 3. Anything clearly bigger than body text is a heading; the biggest size is the title
    heading_sizes = sorted({s for _, s in fragments if s >= body_size * HEADING_SIZE_RATIO}, reverse=True)
    heading_level = {}
    for text, size in fragments:
        if size in heading_sizes:
            heading_level[text] = "#" if size == heading_sizes[0] else "##"

     # 4. Rebuild the text line by line, adding Markdown heading markers
    lines = []
    for line in "\n".join(pages_text).splitlines():
        stripped = line.strip()
        if stripped in heading_level:
            lines.extend(["", f"{heading_level[stripped]} {stripped}", ""])
        elif stripped.startswith(PDF_BULLETS):
            lines.append(f"- {stripped[1:].strip()}")
        else:
            lines.append(stripped)
    return _tidy("\n".join(lines))


# ---------- Word (.docx) ----------

def docx_to_markdown(data: bytes) -> str:
    """Convert a Word document to Markdown using its heading styles, lists and tables."""
    doc = DocxDocument(io.BytesIO(data))
    lines = []

    for block in doc.iter_inner_content():  # paragraphs and tables, in document order
        if isinstance(block, DocxTable):
            lines.extend(_docx_table_to_markdown(block))
            continue

        text = block.text.strip()
        if not text:
            continue
        style = (block.style.name if block.style is not None else "").lower()

        if style in ("title", "heading 1"):
            lines.extend(["", f"# {text}", ""])
        elif style.startswith("heading"):
            lines.extend(["", f"## {text}", ""])
        elif "list" in style:
            lines.append(f"- {text}")
        elif _is_bold_heading(block, text):
            # Fallback for documents where headings were made bold instead of styled
            lines.extend(["", f"## {text}", ""])
        else:
            lines.append(text)

    return _tidy("\n".join(lines))


def _docx_table_to_markdown(table: DocxTable) -> list[str]:
    rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
    if not rows:
        return []
    lines = ["", "| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
    lines += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    return lines + [""]


def _is_bold_heading(paragraph, text: str) -> bool:
    runs = [r for r in paragraph.runs if r.text.strip()]
    return (
        bool(runs)
        and all(r.bold for r in runs)
        and len(text) <= MAX_HEADING_LENGTH
        and not text.endswith((".", ":"))
    )


# ---------- Helpers ----------

def _tidy(text: str) -> str:
    """Collapse runs of blank lines."""
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"
