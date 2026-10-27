"""Where pdfa-bench finds its tools and writes its results.

Inside the image every path is fixed (see the Dockerfile). On a Mac the same code finds macOS's
fonts and ColorSync profiles and Homebrew's tools, so the harness also runs outside the container;
the image is the reference environment, because font and ICC profile differ between the two.
Every path can be overridden with the environment variable named next to it.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _first(env: str, *candidates: str) -> Path:
    value = os.environ.get(env)
    if value:
        return Path(value)
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return Path(candidate)
    return Path(candidates[0])


_GS_ICC = [
    "/opt/gs/share/ghostscript/iccprofiles",
    *sorted(glob.glob("/opt/homebrew/Cellar/ghostscript/*/share/ghostscript/iccprofiles")),
]

OUT = Path(os.environ.get("PDFA_BENCH_OUT", REPO / "results")).resolve()  # PDFA_BENCH_OUT
CORPUS = OUT / "corpus"
OUTPUTS = OUT / "out"
MASTERS = OUT / "masters"
DIFFS = OUT / "diff"
FONT = _first(
    "PDFA_BENCH_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf"
)
SRGB = _first(
    "PDFA_BENCH_SRGB", "/System/Library/ColorSync/Profiles/sRGB Profile.icc", *(f"{d}/srgb.icc" for d in _GS_ICC)
)
CMYK = _first(
    "PDFA_BENCH_CMYK",
    "/System/Library/ColorSync/Profiles/Generic CMYK Profile.icc",
    *(f"{d}/default_cmyk.icc" for d in _GS_ICC),
)
JVM = _first("PDFA_BENCH_JVM", "/opt/pdfa-bench/jvm", str(REPO / ".jvm"))
VERAPDF_CORPUS = _first(
    "PDFA_BENCH_VERAPDF_CORPUS",
    "/opt/pdfa-bench/verapdf-corpus/PDF_A-2b",
    str(REPO / ".cache" / "verapdf-corpus" / "PDF_A-2b"),
)
SLIDES = _first("PDFA_BENCH_SLIDES", "/opt/pdfa-bench/slides", str(REPO / ".cache" / "slides"))  # slides/make_slides.py
OCR_LANGUAGES = os.environ.get("PDFA_BENCH_OCR_LANG", "deu+eng")  # lane P7 only

# Time limits for the external tools, in seconds (tools.run_tool). Generous on purpose: only a hang should
# reach them, and a tool that does is recorded as a failure for that one file.
TOOL_TIMEOUT = 600  # one call on one file: Ghostscript, the PDFBox stamper, pdftotext, pdftoppm, qpdf, pdfsig
OCR_TIMEOUT = 1800  # OCRmyPDF on one file, its own OCR included (lane P7)
VERAPDF_TIMEOUT = 3600  # veraPDF on one batch of up to vera.BATCH files, one JVM
VERSION_TIMEOUT = 60  # a tool's version line, for the environment record
