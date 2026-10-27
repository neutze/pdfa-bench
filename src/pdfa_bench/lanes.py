"""The lanes: each is one way to put an invisible text layer into a PDF and mark it PDF/A.

Every lane gets the same original and the same word boxes, so the method is the only variable.
  P0   control: the original, copied byte for byte
  P1   graft the text layer with pikepdf, then Ghostscript PDF/A-2b with its defaults
  P2   the same Ghostscript call told to pass images through and never auto-rotate
  P2r  P2, with Ghostscript told to abort rather than drop what cannot conform
  P3   pikepdf graft (text layer as an overlay XObject) + PDF/A markers, streams left as they were
  P4   PyMuPDF graft + PDF/A markers, saved incrementally (original bytes kept as the prefix)
  P5   PyMuPDF graft + PDF/A markers, full rewrite with garbage collection and content cleaning
  P6   reference: rasterise every page and lay invisible text over the image
  P7   comparison: OCRmyPDF 17 --mode skip --output-type pdfa, what Paperless runs (its own OCR)
  K1   Kotlin + PDFBox 3 append, full save
  K1i  Kotlin + PDFBox 3 append, saveIncremental

`judge.verify()` decides whether an output survived.
"""

from __future__ import annotations

import html
import re
import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

import fitz
import pikepdf

from . import config
from .common import (
    device_colour_spaces,
    displayed_size,
    displayed_to_user,
    output_intent_for,
    page_geometry,
    pdfa_blockers,
    words_tsv,
)
from .mupdf import fix_cid_fonts
from .tools import ToolTimeout, run_tool

STAMPER_JAR = config.JVM / "pdfa-stamper.jar"
JVM_LIBS = ("pdfbox-3", "pdfbox-io-3", "fontbox-3", "xmpbox-3", "commons-logging")

MODES = ("naive", "triage")

# Fixed before any run (plan, "Metrics").
BASELINE = 0.22  # baseline sits this far up from the bottom of the word box
FONT_MIN, FONT_MAX = 0.4, 1.6
RASTER_DPI = 200

GS_BASE = [
    "gs",
    "-dPDFA=2",
    "-dBATCH",
    "-dNOPAUSE",
    "-dSAFER",
    "-sDEVICE=pdfwrite",
    "-sColorConversionStrategy=RGB",
    "-dPDFACompatibilityPolicy=1",
]
GS_CAREFUL = [
    "-dAutoRotatePages=/None",
    "-dPassThroughJPEGImages=true",
    "-dPassThroughJPXImages=true",
    "-dDownsampleColorImages=false",
    "-dDownsampleGrayImages=false",
    "-dDownsampleMonoImages=false",
    "-dAutoFilterColorImages=false",
    "-dAutoFilterGrayImages=false",
    "-dColorImageFilter=/FlateEncode",
    "-dGrayImageFilter=/FlateEncode",
]
# The ICC profile goes into the PostScript as hex, as OCRmyPDF does. Reading it from a file under
# -dSAFER failed even with --permit-file-read (Ghostscript 10.08.0, measured 2026-09-25): Ghostscript
# printed "PDF/A processing aborted", exited 0, and wrote a file with no OutputIntent.
GS_PDFA_DEF = """%!
[/_objdef {{icc_PDFA}} /type /stream /OBJ pdfmark
[{{icc_PDFA}} <</N 3>> /PUT pdfmark
[{{icc_PDFA}} <{icc_hex}> /PUT pdfmark
[/_objdef {{OutputIntent_PDFA}} /type /dict /OBJ pdfmark
[{{OutputIntent_PDFA}} <</Type /OutputIntent /S /GTS_PDFA1 /DestOutputProfile {{icc_PDFA}}
  /OutputConditionIdentifier (sRGB IEC61966-2.1)>> /PUT pdfmark
[{{Catalog}} <</OutputIntents [ {{OutputIntent_PDFA}} ]>> /PUT pdfmark
"""
# Output intents by the device colour a file paints with (common.output_intent_for).
INTENTS = {
    "rgb": (config.SRGB, 3, "sRGB IEC61966-2.1", "http://www.color.org"),
    "cmyk": (config.CMYK, 4, "Custom", ""),
}
NOT_CONFORMABLE = "not conformable without rewrite"
_PDF_DATE = re.compile(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?([Zz+\-])?(\d{2})?'?(\d{2})?'?")


def pdf_date_iso(value: str) -> str | None:
    match = _PDF_DATE.match(value or "")
    if not match:
        return None
    year, month, day, hour, minute, second, zone, zone_h, zone_m = match.groups()
    iso = f"{year}-{month or '01'}-{day or '01'}T{hour or '00'}:{minute or '00'}:{second or '00'}"
    if zone in ("Z", "z"):
        iso += "Z"
    elif zone in ("+", "-"):
        iso += f"{zone}{zone_h or '00'}:{zone_m or '00'}"
    return iso


def xmp_from_info(info: dict[str, str]) -> str:
    """PDF/A-2b XMP carrying the same values as the Info dictionary (PyMuPDF's `doc.metadata` keys)."""
    esc = html.escape
    fields = ["<pdfaid:part>2</pdfaid:part><pdfaid:conformance>B</pdfaid:conformance>"]
    if info.get("title"):
        fields.append(
            f'<dc:title><rdf:Alt><rdf:li xml:lang="x-default">{esc(info["title"])}</rdf:li></rdf:Alt></dc:title>'
        )
    if info.get("author"):
        fields.append(f"<dc:creator><rdf:Seq><rdf:li>{esc(info['author'])}</rdf:li></rdf:Seq></dc:creator>")
    if info.get("subject"):
        fields.append(
            f'<dc:description><rdf:Alt><rdf:li xml:lang="x-default">{esc(info["subject"])}</rdf:li></rdf:Alt></dc:description>'
        )
    if info.get("keywords"):
        fields.append(f"<pdf:Keywords>{esc(info['keywords'])}</pdf:Keywords>")
    if info.get("creator"):
        fields.append(f"<xmp:CreatorTool>{esc(info['creator'])}</xmp:CreatorTool>")
    if info.get("producer"):
        fields.append(f"<pdf:Producer>{esc(info['producer'])}</pdf:Producer>")
    for key, prop in (("creationDate", "xmp:CreateDate"), ("modDate", "xmp:ModifyDate")):
        iso = pdf_date_iso(info.get(key, ""))
        if iso:
            fields.append(f"<{prop}>{iso}</{prop}>")
    return (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:pdf="http://ns.adobe.com/pdf/1.3/"'
        ' xmlns:xmp="http://ns.adobe.com/xap/1.0/">'
        + "".join(fields)
        + '</rdf:Description></rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
    )


# --- Availability -------------------------------------------------------------------


def jvm_missing() -> list[str]:
    missing = []
    if not shutil.which("java"):
        missing.append("java")
    libs = [p.name for p in (config.JVM / "lib").glob("*.jar")] if (config.JVM / "lib").exists() else []
    missing += [
        f"{config.JVM / 'lib'}/{prefix}*.jar"
        for prefix in JVM_LIBS
        if not any(name.startswith(prefix) for name in libs)
    ]
    if not STAMPER_JAR.exists():
        missing.append(f"{STAMPER_JAR} (make jvm)")
    return missing


def missing_tools(*tools: str) -> list[str]:
    return [tool for tool in tools if not shutil.which(tool)]


# --- Stamping -------------------------------------------------------------------------


def insert_words(page: fitz.Page, font: fitz.Font, geo: dict[str, Any], words: list[dict[str, Any]]) -> int:
    """Invisible words on a fresh, unrotated PyMuPDF page whose MediaBox equals geo's MediaBox."""
    mx0, my0, mx1, my1 = geo["mediabox"]
    width, height = displayed_size(geo)
    placed = 0
    for word in words:
        # Characters the font cannot draw would be written as .notdef glyphs, which PDF/A forbids
        # (veraPDF 6.2.11.8; measured 2026-09-28 on slide bullets, arrows and emoji). PDFBox refuses them.
        text = "".join(c for c in word["text"].strip() if font.has_glyph(ord(c)))
        if not text:
            continue
        u0, v0, u1, v1 = word["box"]
        box_w, box_h = (u1 - u0) * width, (v1 - v0) * height
        x, y = displayed_to_user(u0, v1 - BASELINE * (v1 - v0), geo)
        unit = font.text_length(text, fontsize=1)
        size = box_w / unit if unit > 0 else box_h
        size = min(max(size, box_h * FONT_MIN, 1.0), max(box_h * FONT_MAX, 1.0))
        page.insert_text(
            (x - mx0, (my1 - my0) - (y - my0)),
            text,
            fontsize=size,
            fontname="F0",
            fontfile=str(config.FONT),
            render_mode=3,
            rotate=geo["rotate"],
        )
        placed += 1
    return placed


def build_overlay(original: Path, pages: list[list[dict[str, Any]]], out: Path) -> int:
    """One overlay page per original page, same MediaBox, words placed in that page's user space."""
    with pikepdf.open(original) as pdf:
        geos = [page_geometry(page) for page in pdf.pages]
    font = fitz.Font(fontfile=str(config.FONT))
    placed = 0
    with fitz.open() as doc:
        for index, geo in enumerate(geos):
            mx0, my0, mx1, my1 = geo["mediabox"]
            page = doc.new_page(width=mx1 - mx0, height=my1 - my0)
            placed += insert_words(page, font, geo, pages[index] if index < len(pages) else [])
        doc.subset_fonts()
        fix_cid_fonts(doc)
        doc.save(out, garbage=3, deflate=True)
    return placed


def mark_pdfa_pikepdf(pdf: pikepdf.Pdf) -> str:
    """Output intent chosen by device colour, XMP synced from Info. Page content is not touched;
    a file that could only conform by rewriting it is left unmarked."""
    if pdfa_blockers(pdf):
        return NOT_CONFORMABLE
    intent = output_intent_for(device_colour_spaces(pdf))
    if "/OutputIntents" not in pdf.Root:
        profile, channels, identifier, registry = INTENTS[intent]
        icc = pdf.make_stream(profile.read_bytes())
        icc.N = channels
        entry = pikepdf.Dictionary(
            Type=pikepdf.Name.OutputIntent,
            S=pikepdf.Name.GTS_PDFA1,
            DestOutputProfile=icc,
            OutputConditionIdentifier=pikepdf.String(identifier),
            Info=pikepdf.String(profile.stem),
        )
        if registry:
            entry.RegistryName = pikepdf.String(registry)
        pdf.Root.OutputIntents = pikepdf.Array([pdf.make_indirect(entry)])
    with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
        meta.load_from_docinfo(pdf.docinfo, raise_failure=False)
        meta["pdfaid:part"] = "2"
        meta["pdfaid:conformance"] = "B"
    return f"marked {intent}"


def mark_pdfa_fitz(doc: fitz.Document, spaces: set[str], blocked: bool = False) -> str:
    """The same recipe for PyMuPDF documents. `spaces` comes from the original plus the overlay,
    because the document being written is not open in pikepdf."""
    intent = output_intent_for(spaces)
    if intent is None or blocked:
        return NOT_CONFORMABLE
    catalog = doc.pdf_catalog()
    if doc.xref_get_key(catalog, "OutputIntents")[0] == "null":
        profile, channels, identifier, registry = INTENTS[intent]
        icc = doc.get_new_xref()
        doc.update_object(icc, f"<</N {channels}>>")
        doc.update_stream(icc, profile.read_bytes(), new=True)
        entry = doc.get_new_xref()
        registry_key = f"/RegistryName({registry})" if registry else ""
        doc.update_object(
            entry,
            f"<</Type/OutputIntent/S/GTS_PDFA1/DestOutputProfile {icc} 0 R"
            f"/OutputConditionIdentifier({identifier})/Info({profile.stem}){registry_key}>>",
        )
        doc.xref_set_key(catalog, "OutputIntents", f"[{entry} 0 R]")
    doc.set_xml_metadata(xmp_from_info(doc.metadata or {}))
    return f"marked {intent}"


def graft_pikepdf(original: Path, overlay: Path, pages: list[list[dict[str, Any]]], out: Path, markers: bool) -> None:
    with pikepdf.open(original) as pdf, pikepdf.open(overlay) as layer:
        for index, page in enumerate(pdf.pages):
            if index < len(pages) and pages[index]:
                # Not Page.add_overlay: it counter-rotates the overlay on /Rotate pages (qpdf's
                # invert_transformations), and this overlay is already in unrotated user space.
                # Measured 2026-09-15: add_overlay put 0% of words in their boxes at /Rotate 90.
                formx = pdf.copy_foreign(layer.pages[index].as_form_xobject())
                name = page.add_resource(formx, pikepdf.Name.XObject, prefix="Fx")
                rect = pikepdf.Rectangle(*page_geometry(page)["mediabox"])
                placement = page.calc_form_xobject_placement(
                    formx, name, rect, invert_transformations=False, allow_shrink=True, allow_expand=True
                )
                page.contents_add(b"q\n", prepend=True)
                page.contents_add(b"\nQ\n" + placement)
        if markers:
            mark_pdfa_pikepdf(pdf)
        # Leave every existing stream exactly as encoded: qpdf would otherwise inflate and re-deflate
        # them, keeping pixels but not bytes (measured 2026-09-25 on an alpha-channel scan).
        pdf.save(out, stream_decode_level=pikepdf.StreamDecodeLevel.none, compress_streams=False)


def graft_fitz(original: Path, overlay: Path, pages: list[list[dict[str, Any]]], out: Path, incremental: bool) -> None:
    # An incremental save appends to the file it opened, so that is `out` itself; a full rewrite opens a
    # copy beside it and writes `out` fresh. The copy goes on failure too, not only on success.
    target = out if incremental else out.with_suffix(".work.pdf")
    shutil.copyfile(original, target)
    try:
        with (
            fitz.open(target) as doc,
            fitz.open(overlay) as layer,
            pikepdf.open(original) as pdf,
            pikepdf.open(overlay) as layer_pdf,
        ):
            geos = [page_geometry(page) for page in pdf.pages]
            spaces = device_colour_spaces(pdf) | device_colour_spaces(layer_pdf)
            blocked = bool(pdfa_blockers(pdf))
            for index, page in enumerate(doc):
                if index < len(pages) and pages[index]:
                    # The MediaBox in PDF user space, taken into PyMuPDF's page space by its own matrix.
                    # Measured 2026-09-15: the only rect of five that placed 100% of words at every
                    # /Rotate with and without an offset CropBox.
                    rect = fitz.Rect(*geos[index]["mediabox"]) * page.transformation_matrix
                    page.show_pdf_page(rect, layer, index, keep_proportion=False, overlay=True)
            mark_pdfa_fitz(doc, spaces, blocked)
            if incremental:
                doc.saveIncr()
            else:
                doc.save(out, garbage=4, deflate=True, clean=True)
    finally:
        if not incremental:
            target.unlink(missing_ok=True)


def rasterise(original: Path, pages: list[list[dict[str, Any]]], out: Path) -> None:
    font = fitz.Font(fontfile=str(config.FONT))
    with fitz.open(original) as src, fitz.open() as doc:
        for index, page in enumerate(src):
            pix = page.get_pixmap(dpi=RASTER_DPI, alpha=False)
            width, height = page.rect.width, page.rect.height
            new = doc.new_page(width=width, height=height)
            new.insert_image(new.rect, pixmap=pix)
            geo = {"mediabox": [0, 0, width, height], "cropbox": [0, 0, width, height], "rotate": 0}
            insert_words(new, font, geo, pages[index] if index < len(pages) else [])
        mark_pdfa_fitz(doc, {"RGB", "Gray"})
        doc.subset_fonts()
        fix_cid_fonts(doc)
        doc.save(out, garbage=3, deflate=True)


def ghostscript(stamped: Path, out: Path, careful: bool, abort: bool) -> str | None:
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "PDFA_def.ps").write_text(
            GS_PDFA_DEF.format(icc_hex=config.SRGB.read_bytes().hex()), encoding="ascii"
        )
        command = list(GS_BASE)
        if careful:
            command += GS_CAREFUL
        if abort:
            command = [c for c in command if not c.startswith("-dPDFACompatibilityPolicy")] + [
                "-dPDFACompatibilityPolicy=2"
            ]
        # Absolute path: given a bare "PDFA_def.ps", Ghostscript searches its own library first and runs
        # its bundled template, which fails to open an ICC file and aborts PDF/A (measured 2026-09-25).
        command += [f"-sOutputFile={out}", str(Path(tmp) / "PDFA_def.ps"), str(stamped)]
        completed = run_tool(command, config.TOOL_TIMEOUT)
    if completed.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        out.unlink(missing_ok=True)
        return f"gs exit {completed.returncode}"
    if "PDF/A processing aborted" in completed.stdout + completed.stderr:
        out.unlink(missing_ok=True)  # Ghostscript exits 0 here; the file is not what was asked for
        return "gs aborted PDF/A"
    return None


def kotlin(original: Path, pages: list[list[dict[str, Any]]], out: Path, incremental: bool) -> str | None:
    """PDFBox follows the same recipe: the intent is decided here, from the same census, and passed in."""
    with pikepdf.open(original) as pdf:
        intent = "none" if pdfa_blockers(pdf) else output_intent_for(device_colour_spaces(pdf))
    with tempfile.TemporaryDirectory() as tmp:
        tsv = Path(tmp) / "words.tsv"
        tsv.write_text(words_tsv(pages), encoding="utf-8")
        classpath = f"{config.JVM / 'lib'}/*:{STAMPER_JAR}"
        completed = run_tool(
            [
                "java",
                "-cp",
                classpath,
                "PdfaStamperKt",
                "incremental" if incremental else "full",
                str(original),
                str(tsv),
                str(out),
                str(config.FONT),
                intent,
                str(config.SRGB),
                str(config.CMYK),
            ],
            config.TOOL_TIMEOUT,
        )
    if completed.returncode != 0 or not out.exists():
        out.unlink(missing_ok=True)
        return f"java exit {completed.returncode}"
    return None


def ocrmypdf(original: Path, out: Path) -> str | None:
    """Lane P7: OCRmyPDF's own pipeline, OCR included -- the comparison, not a contender for the recipe."""
    completed = run_tool(
        [
            "ocrmypdf",
            "--mode",
            "skip",
            "--output-type",
            "pdfa",
            "-l",
            config.OCR_LANGUAGES,
            "--quiet",
            str(original),
            str(out),
        ],
        config.OCR_TIMEOUT,
    )
    if completed.returncode != 0 or not out.exists():
        out.unlink(missing_ok=True)
        return f"ocrmypdf exit {completed.returncode}"
    return None


# --- The lanes ------------------------------------------------------------------------


Pages = list[list[dict[str, Any]]]


def _nothing_missing() -> list[str]:
    return []


@dataclass(frozen=True)
class Lane:
    """One way of making an output. `produce(original, pages, out)` writes `out` and returns a failure
    note or None; `needs()` lists what this machine lacks to run it."""

    label: str
    role: str  # "ranked", or a lane reported but never ranked: "control", "reference", "comparison"
    produce: Callable[[Path, Pages, Path], str | None]
    needs: Callable[[], list[str]] = _nothing_missing

    def meta(self) -> dict[str, str]:
        """What results.json records about the lane."""
        return {"label": self.label, "role": self.role}


def _copy(original: Path, pages: Pages, out: Path) -> None:
    shutil.copyfile(original, out)


def _with_overlay(original: Path, pages: Pages, then: Callable[[Path, Path], str | None]) -> str | None:
    """Build the text-layer overlay in a temporary folder, then hand (folder, overlay) on."""
    with tempfile.TemporaryDirectory() as tmp:
        overlay = Path(tmp) / "overlay.pdf"
        build_overlay(original, pages, overlay)
        return then(Path(tmp), overlay)


def _pikepdf_lane(original: Path, pages: Pages, out: Path) -> str | None:
    return _with_overlay(
        original, pages, lambda tmp, overlay: graft_pikepdf(original, overlay, pages, out, markers=True)
    )


def _ghostscript_lane(original: Path, pages: Pages, out: Path, *, careful: bool, abort: bool) -> str | None:
    def convert(tmp: Path, overlay: Path) -> str | None:
        stamped = tmp / "stamped.pdf"
        graft_pikepdf(original, overlay, pages, stamped, markers=False)
        return ghostscript(stamped, out, careful=careful, abort=abort)

    return _with_overlay(original, pages, convert)


def _fitz_lane(original: Path, pages: Pages, out: Path, *, incremental: bool) -> str | None:
    return _with_overlay(
        original, pages, lambda tmp, overlay: graft_fitz(original, overlay, pages, out, incremental=incremental)
    )


def _ocrmypdf_lane(original: Path, pages: Pages, out: Path) -> str | None:
    return ocrmypdf(original, out)  # its own OCR: the word boxes are not used


_needs_gs = partial(missing_tools, "gs")
LANES: dict[str, Lane] = {
    "P0": Lane("control: the original, unchanged", "control", _copy),
    "P1": Lane(
        "pikepdf stamp, then Ghostscript PDF/A (defaults)",
        "ranked",
        partial(_ghostscript_lane, careful=False, abort=False),
        _needs_gs,
    ),
    "P2": Lane(
        "same, Ghostscript images passed through",
        "ranked",
        partial(_ghostscript_lane, careful=True, abort=False),
        _needs_gs,
    ),
    "P2r": Lane(
        "same, Ghostscript aborts instead of dropping",
        "ranked",
        partial(_ghostscript_lane, careful=True, abort=True),
        _needs_gs,
    ),
    "P3": Lane("pikepdf overlay + PDF/A markers", "ranked", _pikepdf_lane),
    "P4": Lane("PyMuPDF overlay, incremental save", "ranked", partial(_fitz_lane, incremental=True)),
    "P5": Lane("PyMuPDF overlay, full rewrite + clean", "ranked", partial(_fitz_lane, incremental=False)),
    "P6": Lane("reference: rasterise pages + text", "reference", rasterise),
    "P7": Lane(
        "comparison: OCRmyPDF (what Paperless runs)", "comparison", _ocrmypdf_lane, partial(missing_tools, "ocrmypdf")
    ),
    "K1": Lane("Kotlin + PDFBox, full save", "ranked", partial(kotlin, incremental=False), jvm_missing),
    "K1i": Lane("Kotlin + PDFBox, incremental save", "ranked", partial(kotlin, incremental=True), jvm_missing),
}


def lane_missing(lane: str) -> list[str]:
    definition = LANES.get(lane)
    return definition.needs() if definition else []


def run_lane(lane: str, original: Path, pages: Pages, out: Path) -> str | None:
    """Produce `out` from `original`. Returns a failure note, or None. A failure leaves no output.
    The note is the exception's name, the tool's exit code, or "<tool> timed out after <n>s"."""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.unlink(missing_ok=True)
    try:
        definition = LANES.get(lane)
        if definition is None:
            raise ValueError(lane)
        return definition.produce(original, pages, out)
    except ToolTimeout as exc:
        out.unlink(missing_ok=True)
        return str(exc)
    except Exception as exc:  # recorded, not retried
        out.unlink(missing_ok=True)
        return f"{type(exc).__name__}"
