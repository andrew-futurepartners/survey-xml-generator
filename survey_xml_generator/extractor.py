"""Stage 1: Raw .docx extraction.

Reads a Word document and produces a flat list of "block" dicts that
downstream AI stages consume. No AI calls here -- purely deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import List, Optional

from docx import Document
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.text.paragraph import Paragraph
from docx.table import Table


# ---------------------------------------------------------------------------
# Block types
# ---------------------------------------------------------------------------

class BlockType(str, Enum):
    PARAGRAPH = "paragraph"
    PAGEBREAK = "pagebreak"
    BLOCK_MARKER = "block_marker"
    TABLE = "table"


@dataclass
class TextBlock:
    """A single paragraph extracted from the document."""
    block_type: str = BlockType.PARAGRAPH
    index: int = 0
    text: str = ""
    style: str = ""
    bold: bool = False
    italic: bool = False
    underline: bool = False
    is_list_item: bool = False
    indent_level: int = 0
    # Set when we detect a page-break marker in the text
    is_pagebreak: bool = False


@dataclass
class TableBlock:
    """A table extracted from the document."""
    block_type: str = BlockType.TABLE
    index: int = 0
    rows: List[List[str]] = field(default_factory=list)
    header_row: List[str] = field(default_factory=list)
    num_rows: int = 0
    num_cols: int = 0


# ---------------------------------------------------------------------------
# Pagebreak detection patterns (covers common conventions)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Windows-1252 → Unicode normalization
# ---------------------------------------------------------------------------
# Word sometimes stores smart punctuation as raw Windows-1252 byte values
# (0x80–0x9F range) which are C1 control characters in Unicode, not the
# intended glyphs.  This map corrects them.

_CP1252_TO_UNICODE = str.maketrans({
    0x82: '\u201A',  # ‚  single low-9 quotation mark
    0x83: '\u0192',  # ƒ  latin small letter f with hook
    0x84: '\u201E',  # „  double low-9 quotation mark
    0x85: '\u2026',  # …  horizontal ellipsis
    0x86: '\u2020',  # †  dagger
    0x87: '\u2021',  # ‡  double dagger
    0x88: '\u02C6',  # ˆ  modifier letter circumflex accent
    0x89: '\u2030',  # ‰  per mille sign
    0x8A: '\u0160',  # Š  latin capital letter s with caron
    0x8B: '\u2039',  # ‹  single left-pointing angle quotation mark
    0x8C: '\u0152',  # Œ  latin capital ligature oe
    0x91: '\u2018',  # '  left single quotation mark
    0x92: '\u2019',  # '  right single quotation mark
    0x93: '\u201C',  # "  left double quotation mark
    0x94: '\u201D',  # "  right double quotation mark
    0x95: '\u2022',  # •  bullet
    0x96: '\u2013',  # –  en dash
    0x97: '\u2014',  # —  em dash
    0x98: '\u02DC',  # ˜  small tilde
    0x99: '\u2122',  # ™  trade mark sign
    0x9A: '\u0161',  # š  latin small letter s with caron
    0x9B: '\u203A',  # ›  single right-pointing angle quotation mark
    0x9C: '\u0153',  # œ  latin small ligature oe
})


_TYPOGRAPHIC_TO_ASCII = str.maketrans({
    0x2018: "'",   # '  left single quotation mark
    0x2019: "'",   # '  right single quotation mark
    0x201A: "'",   # ‚  single low-9 quotation mark
    0x201C: '"',   # "  left double quotation mark
    0x201D: '"',   # "  right double quotation mark
    0x201E: '"',   # „  double low-9 quotation mark
    0x2013: '-',   # –  en dash
    0x2014: '-',   # —  em dash
    0x2026: '...', # …  horizontal ellipsis
    0x00A0: ' ',   #    non-breaking space
    0x200B: '',    #    zero-width space
    0x200C: '',    #    zero-width non-joiner
    0x200D: '',    #    zero-width joiner
    0xFEFF: '',    #    byte-order mark / zero-width no-break space
})


def _clean_text(text: str) -> str:
    """Normalise Windows-1252 artefacts and typographic Unicode to plain ASCII."""
    return text.translate(_CP1252_TO_UNICODE).translate(_TYPOGRAPHIC_TO_ASCII)


# ---------------------------------------------------------------------------
# Pagebreak detection patterns (covers common conventions)
# ---------------------------------------------------------------------------

_PAGEBREAK_PATTERNS = [
    re.compile(r"^<<\s*PAGE\s*BREAK\s*>>$", re.IGNORECASE),
    re.compile(r"^---\s*PAGEBREAK\s*---$", re.IGNORECASE),
    re.compile(r"^---\s*PAGE\s*BREAK\s*---$", re.IGNORECASE),
    re.compile(r"^\[PAGE\s*BREAK\]$", re.IGNORECASE),
    re.compile(r"^PAGE\s*BREAK$", re.IGNORECASE),
]


def _is_pagebreak(text: str) -> bool:
    """Check if a line is a page-break marker."""
    stripped = text.strip()
    return any(p.match(stripped) for p in _PAGEBREAK_PATTERNS)


# ---------------------------------------------------------------------------
# Block-marker detection
# ---------------------------------------------------------------------------

_BLOCK_MARKER_PATTERN = re.compile(
    r"^\[(BLOCK|RANDOMIZE)\b.*\]$", re.IGNORECASE
)


def _is_block_marker(text: str) -> bool:
    """Check if a line is a survey block marker like [BLOCK SUN CHASERS]."""
    return bool(_BLOCK_MARKER_PATTERN.match(text.strip()))


def _parse_block_marker(text: str) -> str:
    """Extract the block name from a marker like '[BLOCK SUN CHASERS]'.

    Strips the outer brackets and the leading 'BLOCK ' prefix.
    For markers with embedded conditions (e.g. '[BLOCK WILD BUNCH: IF ...]'),
    only the portion before the colon is used as the name.
    """
    inner = text.strip()[1:-1].strip()  # remove [ and ]
    if inner.upper().startswith("BLOCK "):
        inner = inner[6:].strip()
    # Split on ':' to separate name from embedded conditions
    name = inner.split(":")[0].strip()
    return name


# ---------------------------------------------------------------------------
# Run-level formatting helpers
# ---------------------------------------------------------------------------

def _paragraph_has_bold(para: Paragraph) -> bool:
    """True if the majority of non-whitespace runs are bold."""
    runs = [r for r in para.runs if r.text.strip()]
    if not runs:
        return False
    bold_count = sum(1 for r in runs if r.bold)
    return bold_count > len(runs) / 2


def _paragraph_has_italic(para: Paragraph) -> bool:
    runs = [r for r in para.runs if r.text.strip()]
    if not runs:
        return False
    italic_count = sum(1 for r in runs if r.italic)
    return italic_count > len(runs) / 2


def _paragraph_has_underline(para: Paragraph) -> bool:
    runs = [r for r in para.runs if r.text.strip()]
    if not runs:
        return False
    ul_count = sum(1 for r in runs if r.underline)
    return ul_count > len(runs) / 2


def _is_list_item(para: Paragraph) -> bool:
    """Check if paragraph has list (numPr) formatting."""
    pPr = para._element.find(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pPr"
    )
    if pPr is None:
        return False
    numPr = pPr.find(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}numPr"
    )
    return numPr is not None


def _indent_level(para: Paragraph) -> int:
    """Return the indentation level (0-based) from list numbering or style."""
    pPr = para._element.find(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pPr"
    )
    if pPr is None:
        return 0
    numPr = pPr.find(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}numPr"
    )
    if numPr is not None:
        ilvl = numPr.find(
            "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}ilvl"
        )
        if ilvl is not None:
            return int(ilvl.get(
                "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}val",
                "0"
            ))
    return 0


# ---------------------------------------------------------------------------
# Word section-break detection
# ---------------------------------------------------------------------------

def _has_section_break(para: Paragraph) -> bool:
    """Check if this paragraph's pPr contains a section break (which Word
    uses for actual page breaks inserted via Insert > Page Break)."""
    pPr = para._element.find(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}pPr"
    )
    if pPr is None:
        return False
    sectPr = pPr.find(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}sectPr"
    )
    return sectPr is not None


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

def _iter_block_items(doc: Document):
    """Yield Paragraph and Table objects in document order."""
    for child in doc.element.body.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, doc)
        elif isinstance(child, CT_Tbl):
            yield Table(child, doc)


def extract_blocks(doc: Document) -> List[dict]:
    """Extract all blocks from a Word document.

    Returns a list of dicts (serialisable to JSON) representing every
    paragraph, table, and page break in document order.
    """
    blocks: List[dict] = []
    idx = 0

    for block in _iter_block_items(doc):
        if isinstance(block, Paragraph):
            text = _clean_text(block.text.strip())

            # Skip completely empty paragraphs
            if not text:
                # But check for Word-native page breaks (section breaks)
                if _has_section_break(block):
                    blocks.append({
                        "block_type": BlockType.PAGEBREAK,
                        "index": idx,
                    })
                    idx += 1
                continue

            # Check for text-based page break markers
            if _is_pagebreak(text):
                blocks.append({
                    "block_type": BlockType.PAGEBREAK,
                    "index": idx,
                })
                idx += 1
                continue

            # Check for block markers like [BLOCK SUN CHASERS]
            if _is_block_marker(text):
                blocks.append({
                    "block_type": BlockType.BLOCK_MARKER,
                    "index": idx,
                    "text": text,
                    "block_name": _parse_block_marker(text),
                })
                idx += 1
                continue

            style_name = block.style.name if block.style else "Normal"

            blocks.append({
                "block_type": BlockType.PARAGRAPH,
                "index": idx,
                "text": text,
                "style": style_name,
                "bold": _paragraph_has_bold(block),
                "italic": _paragraph_has_italic(block),
                "underline": _paragraph_has_underline(block),
                "is_list_item": _is_list_item(block) or style_name.lower().startswith("list"),
                "indent_level": _indent_level(block),
            })
            idx += 1

        elif isinstance(block, Table):
            rows_data = []
            for row in block.rows:
                cells = [_clean_text(cell.text.strip()) for cell in row.cells]
                rows_data.append(cells)

            if not rows_data:
                continue

            blocks.append({
                "block_type": BlockType.TABLE,
                "index": idx,
                "rows": rows_data,
                "header_row": rows_data[0] if rows_data else [],
                "num_rows": len(rows_data),
                "num_cols": len(rows_data[0]) if rows_data else 0,
            })
            idx += 1

    return blocks


def extract_from_file(file_path: str) -> List[dict]:
    """Convenience: extract blocks from a .docx file path."""
    doc = Document(file_path)
    return extract_blocks(doc)


def extract_from_bytes(file_bytes) -> List[dict]:
    """Convenience: extract blocks from a file-like object (Streamlit upload)."""
    from io import BytesIO
    doc = Document(BytesIO(file_bytes) if isinstance(file_bytes, bytes) else file_bytes)
    return extract_blocks(doc)


# ---------------------------------------------------------------------------
# CLI test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("Usage: python extractor.py <path_to_docx>")
        sys.exit(1)

    blocks = extract_from_file(sys.argv[1])
    print(json.dumps(blocks, indent=2, default=str))
    print(f"\n--- Extracted {len(blocks)} blocks ---")
