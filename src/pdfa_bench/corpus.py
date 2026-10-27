#!/usr/bin/env python3
"""The built-in corpus: constructed hazard files, plus the slide decks when they have been built.

Every file is drawn by this module -- no real document, no download. Each one carries a
structural feature that PDF/A pipelines are known to damage or drop, plus the word boxes a
perfect OCR engine would return for it, so the lanes are tested on stamping and conversion
rather than on recognition. `bench.py` builds it on first use; by hand:

    PYTHONPATH=src python3 -m pdfa_bench.corpus    # writes <results>/corpus/synthetic/ and its manifest

Not constructed, because each needs a library the image doesn't carry: a valid digital
signature (a signing library) and JBIG2 images (an encoder). See TODO.md.
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path

import fitz
import img2pdf
import pikepdf
from PIL import Image

from . import config
from .base import say, write_json
from .common import bbox_words, write_words
from .mupdf import fix_cid_fonts

CORPUS_VERSION = 2  # bump whenever the files change; bench.py rebuilds a corpus of another version
A4 = (595, 842)
SCAN_DPI = 150
PRODUCER = "ocr-bench synthetic"
XMP_PDFA = (
    '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
    '<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
    '<rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/">'
    "<pdfaid:part>2</pdfaid:part><pdfaid:conformance>B</pdfaid:conformance></rdf:Description>"
    '</rdf:RDF></x:xmpmeta><?xpacket end="w"?>'
)

HEADER = [
    ("Müller & Şahin Steuerberatung GmbH", 60, 50, 16),
    ("Königstraße 12 · 70173 Stuttgart", 60, 70, 9),
    ("Datum: 12.03.2026", 400, 130, 10),
]
BODY = [
    "anbei erhalten Sie die Rechnung Nr. 2026-0417 über 490,00 EUR.",
    "Die Leistungen wurden im Zeitraum 01.02.2026 bis 28.02.2026 erbracht.",
    "Pos   Menge   Einzelpreis   Gesamt",
    "1     14      35,00         490,00",
    "Bitte überweisen Sie den Betrag fristgerecht. Vielen Dank für Ihr Vertrauen.",
]
FOOTER = [
    ("Amtsgericht Stuttgart HRB 123456 · IBAN DE12 3456 7890 1234 5678 90", 60, 790, 7),
    ("Geschäftsführer: Ayşe Öztürk · Seite 1 von 1", 60, 802, 7),
]


def draw_letter(page: fitz.Page, embedded: bool = True) -> None:
    font = {"fontname": "F1", "fontfile": str(config.FONT)} if embedded else {"fontname": "helv"}

    def text(value: str, x: float, y: float, size: float) -> None:
        if not embedded:  # Base-14 Helvetica has no Turkish letters; keep the file honest
            value = value.replace("Ş", "S").replace("ş", "s")
        page.insert_text((x, y), value, fontsize=size, **font)

    for value, x, y, size in HEADER:
        text(value, x, y, size)
    text("Sehr geehrte Frau Yilmaz,", 60, 230, 11)
    for index, line in enumerate(BODY):
        text(line, 60, 260 + index * 22, 11)
    for value, x, y, size in FOOTER:
        text(value, x, y, size)


def new_doc() -> fitz.Document:
    doc = fitz.open()
    doc.set_metadata({"producer": PRODUCER, "creator": PRODUCER})
    return doc


def letter_doc(embedded: bool = True) -> fitz.Document:
    doc = new_doc()
    draw_letter(doc.new_page(width=A4[0], height=A4[1]), embedded)
    return doc


def letter_image(mode: str = "RGB") -> Image.Image:
    with letter_doc() as doc:
        pix = doc[0].get_pixmap(dpi=SCAN_DPI, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert(mode)


def image_bytes(image: Image.Image, fmt: str, **options) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, dpi=(SCAN_DPI, SCAN_DPI), **options)
    return buffer.getvalue()


def scan_doc(stream: bytes, producer: str = PRODUCER) -> fitz.Document:
    doc = new_doc()
    doc.set_metadata({"producer": producer, "creator": producer})
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_image(page.rect, stream=stream)
    return doc


def save(doc: fitz.Document, path: Path, fix_fonts: bool = True) -> None:
    """PyMuPDF writes its CID fonts without /CIDToGIDMap; real exporters (Word, LibreOffice) don't. The
    map is provably Identity for PyMuPDF's fonts, so it is added -- except where the hazard is the point."""
    if fix_fonts:
        fix_cid_fonts(doc)
    doc.save(path)
    doc.close()


def build(out: Path) -> list[dict]:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    jpeg = image_bytes(letter_image(), "JPEG", quality=85)
    entries: list[dict] = []

    def add(key: str, name: str, intended: list[str], words_pages: list) -> None:
        write_words(out / f"{key}.words.json", words_pages)
        entries.append(
            {"key": key, "file": f"{key}.pdf", "words": f"{key}.words.json", "label": name, "intended": intended}
        )

    save(letter_doc(), out / "S01.pdf")
    letter_words = bbox_words(out / "S01.pdf")
    add("S01", "born-digital letter, embedded TrueType", [], letter_words)

    save(letter_doc(embedded=False), out / "S02.pdf")
    add("S02", "born-digital letter, non-embedded Base-14 font", ["non_embedded_fonts"], bbox_words(out / "S02.pdf"))

    save(scan_doc(jpeg), out / "S03.pdf")
    add("S03", "scan, RGB JPEG", ["image_only"], letter_words)

    save(scan_doc(image_bytes(letter_image("CMYK"), "JPEG", quality=85)), out / "S04.pdf")
    add("S04", "scan, CMYK JPEG", ["image_only", "cmyk"], letter_words)

    g4 = image_bytes(letter_image("1"), "TIFF", compression="group4")
    (out / "S05.pdf").write_bytes(img2pdf.convert(g4))
    with pikepdf.open(out / "S05.pdf", allow_overwriting_input=True) as pdf:
        pdf.docinfo["/Producer"] = PRODUCER
        pdf.save(out / "S05.pdf")
    add("S05", "scan, 1-bit CCITT G4", ["image_only", "ccitt"], letter_words)

    rgba = letter_image("RGBA")
    rgba.putalpha(Image.new("L", rgba.size, 235))
    save(scan_doc(image_bytes(rgba, "PNG")), out / "S06.pdf")
    add("S06", "scan, PNG with alpha channel", ["image_only", "alpha_image", "transparency"], letter_words)

    doc = letter_doc()
    doc[0].draw_rect(fitz.Rect(50, 240, 320, 400), color=None, fill=(1, 0.85, 0), fill_opacity=0.35)
    save(doc, out / "S07.pdf")
    add("S07", "born-digital with a translucent highlight", ["transparency"], letter_words)

    doc = letter_doc()
    field = fitz.Widget()
    field.field_type, field.field_name, field.rect, field.field_value = (
        fitz.PDF_WIDGET_TYPE_TEXT,
        "kundennummer",
        fitz.Rect(400, 150, 550, 168),
        "K-4711",
    )
    doc[0].add_widget(field)
    box = fitz.Widget()
    box.field_type, box.field_name, box.rect, box.field_value = (
        fitz.PDF_WIDGET_TYPE_CHECKBOX,
        "einverstanden",
        fitz.Rect(60, 380, 74, 394),
        True,
    )
    doc[0].add_widget(box)
    save(doc, out / "S08.pdf")
    add("S08", "born-digital with form fields", ["acroform", "annotations"], letter_words)

    doc = letter_doc()
    doc[0].add_text_annot((520, 300), "Notiz")
    doc[0].add_highlight_annot(fitz.Rect(60, 250, 300, 264))
    doc[0].insert_link({"kind": fitz.LINK_URI, "from": fitz.Rect(60, 272, 300, 286), "uri": "https://example.org/"})
    doc.set_toc([[1, "Rechnung", 1]])
    save(doc, out / "S09.pdf")
    add("S09", "born-digital with annotations, a link and an outline", ["annotations", "outline"], letter_words)

    save(scan_doc(jpeg), out / "S10.tmp.pdf")
    with pikepdf.open(out / "S10.tmp.pdf") as pdf:
        pdf.save(out / "S10.pdf", encryption=pikepdf.Encryption(owner="bench-owner", user="", R=6))
    (out / "S10.tmp.pdf").unlink()
    add("S10", "scan, AES-256 encrypted with an empty user password", ["image_only", "encrypted"], letter_words)

    doc = scan_doc(jpeg, producer="Tesseract 5 / OCRmyPDF")
    page = doc[0]
    for word in letter_words[0]:
        x0, y0, x1, y1 = word["box"]
        page.insert_text(
            (x0 * A4[0], y1 * A4[1]),
            word["text"][::-1],
            fontsize=max(4.0, (y1 - y0) * A4[1] * 0.8),
            fontname="helv",
            render_mode=3,
        )
    save(doc, out / "S11.pdf")
    add("S11", "scan with a garbage prior OCR layer", ["prior_ocr"], letter_words)

    doc = letter_doc()
    scan_page = doc.new_page(width=A4[0], height=A4[1])
    scan_page.insert_image(scan_page.rect, stream=jpeg)
    save(doc, out / "S12.pdf", fix_fonts=False)
    add("S12", "two pages: born-digital, then a scan", ["mixed"], [letter_words[0], letter_words[0]])

    rotated = image_bytes(letter_image().rotate(90, expand=True), "JPEG", quality=85)
    doc = new_doc()
    page = doc.new_page(width=A4[1], height=A4[0])
    page.insert_image(page.rect, stream=rotated)
    page.set_rotation(90)
    save(doc, out / "S13.pdf")
    add("S13", "scan stored landscape, displayed upright via /Rotate 90", ["image_only", "rotated"], letter_words)

    doc = new_doc()
    page = doc.new_page(width=A4[0] + 60, height=A4[1] + 60)
    visible = fitz.Rect(30, 30, 30 + A4[0], 30 + A4[1])
    page.insert_image(visible, stream=jpeg)
    page.set_cropbox(visible)
    save(doc, out / "S14.pdf")
    add("S14", "scan inside a larger MediaBox, CropBox offset", ["image_only", "cropbox"], letter_words)

    doc = letter_doc()
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", "<</S/JavaScript/JS(void 0)>>")
    save(doc, out / "S15.pdf")
    add("S15", "born-digital with a JavaScript open action", ["javascript"], letter_words)

    doc = letter_doc()
    doc.embfile_add("anhang.txt", b"synthetic attachment\n")
    save(doc, out / "S16.pdf")
    add("S16", "born-digital with an embedded file", ["embedded_files"], letter_words)

    doc = letter_doc()
    doc.set_xml_metadata(XMP_PDFA)
    save(doc, out / "S17.pdf")
    add("S17", "born-digital already claiming PDF/A-2b", ["pdfa_claimed"], letter_words)

    doc = new_doc()
    for _ in range(12):
        page = doc.new_page(width=A4[0], height=A4[1])
        page.insert_image(page.rect, stream=jpeg)
    save(doc, out / "S18.pdf")
    add("S18", "12-page scan, for timing and size", ["image_only"], [letter_words[0]] * 12)

    def uncalibrate(path: Path) -> None:
        """Turn PyMuPDF's ICC-based images into uncalibrated DeviceRGB/DeviceCMYK, as many scanners write them.
        PyMuPDF tags even a plain RGB JPEG with an ICC profile, which makes it calibrated."""
        with pikepdf.open(path, allow_overwriting_input=True) as pdf:
            for obj in pdf.objects:
                if isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") == pikepdf.Name.Image:
                    space = obj.get("/ColorSpace")
                    if isinstance(space, pikepdf.Array) and space[0] == pikepdf.Name.ICCBased:
                        channels = int(space[1].get("/N", 0))
                        if channels in (3, 4):
                            obj.ColorSpace = pikepdf.Name.DeviceRGB if channels == 3 else pikepdf.Name.DeviceCMYK
            pdf.save(path)

    cmyk_jpeg = image_bytes(letter_image("CMYK"), "JPEG", quality=85)
    save(scan_doc(cmyk_jpeg), out / "S19.pdf")
    uncalibrate(out / "S19.pdf")
    add("S19", "scan, uncalibrated DeviceCMYK JPEG", ["image_only", "device_cmyk"], letter_words)

    doc = new_doc()
    page = doc.new_page(width=A4[0], height=A4[1])
    page.insert_image(fitz.Rect(0, 0, A4[0], A4[1] / 2), stream=jpeg)
    page.insert_image(fitz.Rect(0, A4[1] / 2, A4[0], A4[1]), stream=cmyk_jpeg)
    save(doc, out / "S20.pdf")
    uncalibrate(out / "S20.pdf")
    add(
        "S20", "scan, RGB and uncalibrated CMYK images on one page", ["image_only", "mixed_device_colour"], letter_words
    )

    for key, name, label in (
        ("S21", "slides-libreoffice.pdf", "slides: PowerPoint deck exported by LibreOffice"),
        ("S22", "slides-chromium.pdf", "slides: web deck printed by Chromium (Google Slides engine)"),
    ):
        if (config.SLIDES / name).exists():
            shutil.copyfile(config.SLIDES / name, out / f"{key}.pdf")
            add(key, label, [], bbox_words(out / f"{key}.pdf"))
    if (out / "S21.pdf").exists():
        # The same deck saved as pictures -- what "export as images" or a photographed handout gives.
        doc = new_doc()
        with fitz.open(out / "S21.pdf") as deck:
            for slide in deck:
                page = doc.new_page(width=slide.rect.width, height=slide.rect.height)
                page.insert_image(
                    page.rect, stream=slide.get_pixmap(dpi=SCAN_DPI, alpha=False).tobytes("jpg", jpg_quality=85)
                )
        save(doc, out / "S23.pdf")
        add("S23", "slides exported as images, no text layer", ["image_only"], bbox_words(out / "S21.pdf"))

    write_json(out / "manifest.json", {"corpus": "synthetic", "version": CORPUS_VERSION, "files": entries})
    return entries


def main() -> None:
    entries = build(config.CORPUS / "synthetic")
    say(f"synthetic corpus: {len(entries)} files in {config.CORPUS / 'synthetic'}")


if __name__ == "__main__":
    main()
