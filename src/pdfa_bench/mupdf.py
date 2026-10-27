"""PyMuPDF settings and fixes shared by the lanes and the corpus builder."""

from __future__ import annotations

import fitz

# Tagged slide exports make MuPDF print structure-tree warnings to stderr; results are unaffected.
fitz.TOOLS.mupdf_display_errors(False)


def fix_cid_fonts(doc: fitz.Document) -> int:
    """PDF/A-2 6.2.11.3.2: an embedded CIDFontType2 needs /CIDToGIDMap. PyMuPDF writes its subset fonts
    without one, and its CIDs are glyph ids, so /Identity is the true map. Measured 2026-09-25: the only
    rule veraPDF failed on a pikepdf-grafted scan. Touches our own text-layer font, never page content."""
    fixed = 0
    for xref in range(1, doc.xref_length()):
        if (
            doc.xref_get_key(xref, "Subtype") == ("name", "/CIDFontType2")
            and doc.xref_get_key(xref, "CIDToGIDMap")[0] == "null"
        ):
            doc.xref_set_key(xref, "CIDToGIDMap", "/Identity")
            fixed += 1
    return fixed
