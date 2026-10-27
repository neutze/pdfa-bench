"""PDF structure, geometry and triage shared by every lane: census, colour spaces, word boxes.

Coordinates. Word boxes are normalised to [0,1] with a top-left origin on the page *as
displayed* -- CropBox, with /Rotate applied -- which is what Apple Vision returns for a
rendered page and what pdftotext -bbox reports. PDF user space is y-up and unrotated.
`displayed_to_user` is the one place that converts between the two.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from collections.abc import Iterator
from html import unescape
from pathlib import Path
from typing import Any

import pikepdf

from . import config
from .base import read_json, tokens, write_json
from .tools import run_tool

# Producers whose text layer is prior OCR ("right length, garbage content"), not born-digital text.
OCR_PRODUCER_RE = re.compile(
    r"tesseract|ocrmypdf|abbyy|finereader|readiris|omnipage|naps2|scan|epson|canon|brother|fujitsu|kofax|"
    r"xerox|ricoh|konica|kyocera|sharp mx|paperport|genius|scanbot|adobe paper capture",
    re.IGNORECASE,
)
PAGE_TEXT_MIN_CHARS = 20  # a page with fewer extracted characters counts as image-only
REFUSE_HAZARDS = ("encrypted", "signed", "acroform", "javascript", "embedded_files")

_PAGE_RE = re.compile(r'<page width="([\d.]+)" height="([\d.]+)">(.*?)</page>', re.DOTALL)
_WORD_RE = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
_SUBSET_RE = re.compile(r"^[A-Z]{6}\+")
_INVISIBLE_TEXT_RE = re.compile(rb"(?<![\d.])3\s+Tr\b")


# --- Geometry ---------------------------------------------------------------------


def inherited(page_obj: pikepdf.Object, key: str) -> Any:
    node = page_obj
    while node is not None:
        if key in node:
            return node[key]
        node = node.get("/Parent")
    return None


def _box(value: Any) -> list[float]:
    x0, y0, x1, y1 = (float(v) for v in value)
    return [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]


def page_geometry(page: pikepdf.Page) -> dict[str, Any]:
    mediabox = _box(inherited(page.obj, "/MediaBox"))
    crop = inherited(page.obj, "/CropBox")
    cropbox = _box(crop) if crop is not None else list(mediabox)
    cropbox = [
        max(cropbox[0], mediabox[0]),
        max(cropbox[1], mediabox[1]),
        min(cropbox[2], mediabox[2]),
        min(cropbox[3], mediabox[3]),
    ]
    rotate = int(inherited(page.obj, "/Rotate") or 0) % 360
    return {"mediabox": mediabox, "cropbox": cropbox, "rotate": rotate}


def displayed_size(geo: dict[str, Any]) -> tuple[float, float]:
    x0, y0, x1, y1 = geo["cropbox"]
    width, height = x1 - x0, y1 - y0
    return (height, width) if geo["rotate"] in (90, 270) else (width, height)


def displayed_to_user(u: float, v: float, geo: dict[str, Any]) -> tuple[float, float]:
    """Normalised top-left displayed point -> PDF user space (y-up, unrotated)."""
    cx0, cy0, cx1, cy1 = geo["cropbox"]
    cw, ch = cx1 - cx0, cy1 - cy0
    rotate = geo["rotate"]
    if rotate == 90:
        return cx0 + v * cw, cy0 + u * ch
    if rotate == 180:
        return cx1 - u * cw, cy0 + v * ch
    if rotate == 270:
        return cx1 - v * cw, cy1 - u * ch
    return cx0 + u * cw, cy1 - v * ch


def user_to_displayed(x: float, y: float, geo: dict[str, Any]) -> tuple[float, float]:
    """Inverse of displayed_to_user."""
    cx0, cy0, cx1, cy1 = geo["cropbox"]
    cw, ch = cx1 - cx0, cy1 - cy0
    rotate = geo["rotate"]
    if rotate == 90:
        return (y - cy0) / ch, (x - cx0) / cw
    if rotate == 180:
        return (cx1 - x) / cw, (y - cy0) / ch
    if rotate == 270:
        return (cy1 - y) / ch, (cx1 - x) / cw
    return (x - cx0) / cw, (cy1 - y) / ch


# --- Text and words ---------------------------------------------------------------


def pdftotext_pages(path: Path) -> list[str]:
    completed = run_tool(["pdftotext", "-enc", "UTF-8", str(path), "-"], config.TOOL_TIMEOUT)
    pages = completed.stdout.split("\f")
    return pages[:-1] if pages and not pages[-1].strip() else pages


def bbox_words(path: Path) -> list[list[dict[str, Any]]]:
    """Every word pdftotext finds, per page, as normalised displayed boxes.

    Poppler reports points in the *rotated MediaBox* frame (top-left origin, /Rotate applied,
    CropBox ignored), and its page width/height stay unrotated. Measured 2026-09-15 on a
    born-digital page given /Rotate 90 and, separately, an offset CropBox. So each corner is
    taken to user space through the MediaBox and back out through the page's real geometry.
    """
    completed = run_tool(["pdftotext", "-bbox-layout", "-enc", "UTF-8", str(path), "-"], config.TOOL_TIMEOUT)
    with pikepdf.open(path) as pdf:
        geos = [page_geometry(page) for page in pdf.pages]
    pages = []
    for index, (_, _, body) in enumerate(_PAGE_RE.findall(completed.stdout)):
        geo = geos[index] if index < len(geos) else {"mediabox": [0, 0, 1, 1], "cropbox": [0, 0, 1, 1], "rotate": 0}
        media = {**geo, "cropbox": geo["mediabox"]}
        media_w, media_h = displayed_size(media)
        words = []
        for x0, y0, x1, y1, word in _WORD_RE.findall(body):
            corners = [
                user_to_displayed(*displayed_to_user(float(x) / media_w, float(y) / media_h, media), geo)
                for x, y in ((x0, y0), (x1, y1))
            ]
            us, vs = [c[0] for c in corners], [c[1] for c in corners]
            words.append({"text": unescape(word), "box": [min(us), min(vs), max(us), max(vs)]})
        pages.append(words)
    return pages


def write_words(path: Path, pages: list[list[dict[str, Any]]]) -> None:
    write_json(path, {"pages": pages})


def read_words(path: Path) -> list[list[dict[str, Any]]]:
    return read_json(path)["pages"]


def words_tsv(pages: list[list[dict[str, Any]]]) -> str:
    """The Kotlin lane's input: page, x0, y0, x1, y1, text -- no JSON library needed on the JVM."""
    rows = []
    for index, words in enumerate(pages):
        for word in words:
            text = word["text"].replace("\t", " ").replace("\n", " ")
            rows.append("\t".join([str(index), *(f"{v:.6f}" for v in word["box"]), text]))
    return "\n".join(rows) + "\n"


def token_recall(reference: Counter, candidate: Counter) -> float | None:
    total = sum(reference.values())
    if not total:
        return None
    return sum(min(count, candidate[token]) for token, count in reference.items()) / total


def token_counter(text: str) -> Counter:
    return Counter(tokens(text))


# --- Structure ----------------------------------------------------------------------


def _name(value: Any) -> str:
    return str(value) if value is not None else ""


def annotations(pdf: pikepdf.Pdf) -> Iterator[Any]:
    """Every annotation on every page."""
    for page in pdf.pages:
        yield from page.obj.get("/Annots") or []


def _appearance_streams(annot: Any) -> Iterator[pikepdf.Stream]:
    """An annotation's appearance streams: /AP maps to streams, or to state dictionaries of streams."""
    appearance = annot.get("/AP")
    for state in appearance.values() if isinstance(appearance, pikepdf.Dictionary) else []:
        streams = (
            state.values()
            if isinstance(state, pikepdf.Dictionary) and not isinstance(state, pikepdf.Stream)
            else [state]
        )
        for stream in streams:
            if isinstance(stream, pikepdf.Stream):
                yield stream


def iter_resources(pdf: pikepdf.Pdf) -> Iterator[tuple[str, Any]]:
    """(category, object) for every resource reachable from pages, form XObjects, annotation
    appearances and the AcroForm defaults. Resources are often *direct* objects, which
    `pdf.objects` (indirect objects only) never lists."""
    seen: set[tuple[int, int]] = set()
    stack: list[Any] = []
    for page in pdf.pages:
        stack.append(page.obj.get("/Resources"))
        for annot in page.obj.get("/Annots") or []:
            stack += [stream.get("/Resources") for stream in _appearance_streams(annot)]
    acroform = pdf.Root.get("/AcroForm")
    if acroform is not None:
        stack.append(acroform.get("/DR"))
    while stack:
        resources = stack.pop()
        if not isinstance(resources, pikepdf.Dictionary):
            continue
        if resources.is_indirect:
            if resources.objgen in seen:
                continue
            seen.add(resources.objgen)
        for category in ("/Font", "/ExtGState", "/XObject", "/Pattern", "/Shading", "/ColorSpace"):
            group = resources.get(category)
            if not isinstance(group, pikepdf.Dictionary):
                continue
            for obj in group.values():
                if getattr(obj, "is_indirect", False):
                    if obj.objgen in seen:
                        continue
                    seen.add(obj.objgen)
                yield category, obj
                if category in ("/XObject", "/Pattern") and isinstance(obj, pikepdf.Stream):
                    stack.append(obj.get("/Resources"))
                if category == "/Font" and "/Resources" in obj:  # Type 3 glyph procedures
                    stack.append(obj.get("/Resources"))


def font_inventory(pdf: pikepdf.Pdf) -> dict[str, bool]:
    """BaseFont name (subset prefix stripped) -> embedded, over every reachable font."""
    fonts: dict[str, bool] = {}
    for category, obj in iter_resources(pdf):
        if category != "/Font" or not isinstance(obj, pikepdf.Dictionary):
            continue
        subtype = _name(obj.get("/Subtype"))
        if subtype == "/Type3":
            fonts.setdefault("Type3", True)
            continue
        target = obj
        if subtype == "/Type0" and "/DescendantFonts" in obj and len(obj.DescendantFonts):
            target = obj.DescendantFonts[0]
        descriptor = target.get("/FontDescriptor")
        embedded = descriptor is not None and any(k in descriptor for k in ("/FontFile", "/FontFile2", "/FontFile3"))
        name = _SUBSET_RE.sub("", _name(obj.get("/BaseFont")).lstrip("/")) or "unnamed"
        fonts[name] = fonts.get(name, False) or embedded
    return fonts


def image_hashes(pdf: pikepdf.Pdf) -> Counter:
    found: Counter = Counter()
    for obj in pdf.objects:
        if isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") == pikepdf.Name.Image:
            found[hashlib.sha256(obj.read_raw_bytes()).hexdigest()] += 1
    return found


def _count_fields(fields: Any) -> tuple[int, int]:
    total = signatures = 0
    for field in fields or []:
        kids = field.get("/Kids")
        if kids is not None and any("/T" in kid or "/Kids" in kid for kid in kids):
            sub_total, sub_sig = _count_fields(kids)
            total, signatures = total + sub_total, signatures + sub_sig
            continue
        total += 1
        if field.get("/FT") == pikepdf.Name.Sig and field.get("/V") is not None:
            signatures += 1
    return total, signatures


def interactive_inventory(pdf: pikepdf.Pdf) -> dict[str, int]:
    acroform = pdf.Root.get("/AcroForm")
    fields, signatures = _count_fields(acroform.get("/Fields") if acroform is not None else None)
    annots = links = 0
    for annot in annotations(pdf):
        annots += 1
        links += annot.get("/Subtype") == pikepdf.Name.Link
    with pdf.open_outline() as outline:

        def walk(items):
            return sum(1 + walk(item.children) for item in items)

        outline_items = walk(outline.root)
    return {
        "fields": fields,
        "signatures": signatures,
        "annotations": annots,
        "links": links,
        "outline_items": outline_items,
        "attachments": len(pdf.attachments),
    }


def _is_js_action(action: Any) -> bool:
    return isinstance(action, pikepdf.Dictionary) and ("/JS" in action or action.get("/S") == pikepdf.Name.JavaScript)


def has_javascript(pdf: pikepdf.Pdf) -> bool:
    """Document-level scripts, open/additional actions on the catalog, pages and annotations.
    Actions are usually direct objects, so they are walked rather than looked up in pdf.objects."""
    names = pdf.Root.get("/Names")
    if names is not None and "/JavaScript" in names:
        return True
    holders = [pdf.Root] + [page.obj for page in pdf.pages] + list(annotations(pdf))
    for holder in holders:
        if _is_js_action(holder.get("/OpenAction")) or _is_js_action(holder.get("/A")):
            return True
        additional = holder.get("/AA")
        if isinstance(additional, pikepdf.Dictionary) and any(_is_js_action(a) for a in additional.values()):
            return True
    return any(_is_js_action(obj) for obj in pdf.objects if isinstance(obj, pikepdf.Dictionary))


def pdfa_claim(pdf: pikepdf.Pdf) -> str:
    try:
        meta = pdf.open_metadata()
        part, conformance = meta.get("pdfaid:part"), meta.get("pdfaid:conformance")
    except Exception:
        return ""
    return f"{part}{(conformance or '').lower()}" if part else ""


def _is_cmyk(space: Any) -> bool:
    if space == pikepdf.Name.DeviceCMYK:
        return True
    if isinstance(space, pikepdf.Array) and len(space) >= 2 and space[0] == pikepdf.Name.ICCBased:
        return int(space[1].get("/N", 0)) == 4
    return False


_DEVICE_NAMES = {"/DeviceRGB": "RGB", "/DeviceCMYK": "CMYK", "/DeviceGray": "Gray"}
_NUMBER = rb"[-+]?(?:\d+\.?\d*|\.\d+)"
_COLOUR_OPERATORS = (
    ("RGB", re.compile(rb"(?<![\w.])(?:" + _NUMBER + rb"\s+){3}(?:rg|RG)(?![\w])")),
    ("CMYK", re.compile(rb"(?<![\w.])(?:" + _NUMBER + rb"\s+){4}(?:k|K)(?![\w])")),
    ("Gray", re.compile(rb"(?<![\w.])" + _NUMBER + rb"\s+(?:g|G)(?![\w])")),
)
_DEVICE_SPACE_OPERATOR = re.compile(rb"/Device(RGB|CMYK|Gray)\s+(?:cs|CS)(?![\w])")


def _space_devices(space: Any) -> set[str]:
    """Uncalibrated device spaces a colour-space value resolves to. ICC-based, Cal* and Lab are calibrated."""
    if isinstance(space, pikepdf.Name):
        name = str(space)
        return {_DEVICE_NAMES[name]} if name in _DEVICE_NAMES else set()
    if isinstance(space, pikepdf.Array) and len(space):
        kind = str(space[0])
        if kind in _DEVICE_NAMES:
            return {_DEVICE_NAMES[kind]}
        if kind == "/Indexed" and len(space) > 1:
            return _space_devices(space[1])
        if kind in ("/Separation", "/DeviceN") and len(space) > 2:
            return _space_devices(space[2])
    return set()


def _stream_parts(obj: Any) -> list[Any]:
    """/Contents is one stream, an array of streams, or absent."""
    return list(obj) if isinstance(obj, pikepdf.Array) else [obj] if obj is not None else []


def _stream_bytes(obj: Any) -> bytes:
    chunks = []
    for part in _stream_parts(obj):
        try:
            chunks.append(part.read_bytes())
        except Exception:  # a stream that cannot be decoded has nothing to scan
            pass
    return b"\n".join(chunks)


def has_lzw(pdf: pikepdf.Pdf) -> bool:
    return any("LZWDecode" in str(obj.get("/Filter", "")) for obj in pdf.objects if isinstance(obj, pikepdf.Stream))


def device_colour_spaces(pdf: pikepdf.Pdf) -> set[str]:
    """Which uncalibrated device colour spaces a file paints with: {"RGB", "CMYK", "Gray"}.

    PDF/A ties these to the output intent: DeviceRGB needs an RGB intent, DeviceCMYK a CMYK one,
    DeviceGray works with either. Content streams are scanned by operator, not parsed, so this is
    a proxy that errs towards reporting a space; veraPDF has the last word.
    """
    found: set[str] = set()
    contents = [_stream_bytes(page.obj.get("/Contents")) for page in pdf.pages]
    contents += [_stream_bytes(stream) for annot in annotations(pdf) for stream in _appearance_streams(annot)]
    for category, obj in iter_resources(pdf):
        if category == "/ColorSpace":
            found |= _space_devices(obj)
        elif category == "/XObject" and isinstance(obj, pikepdf.Stream):
            if obj.get("/Subtype") == pikepdf.Name.Image:
                if not bool(obj.get("/ImageMask", False)):
                    found |= _space_devices(obj.get("/ColorSpace"))
            elif obj.get("/Subtype") == pikepdf.Name.Form:
                contents.append(_stream_bytes(obj))
        elif category == "/Shading" and isinstance(obj, pikepdf.Dictionary):
            found |= _space_devices(obj.get("/ColorSpace"))
        elif category == "/Pattern" and isinstance(obj, pikepdf.Dictionary):
            shading = obj.get("/Shading")
            if isinstance(shading, pikepdf.Dictionary):
                found |= _space_devices(shading.get("/ColorSpace"))
            if isinstance(obj, pikepdf.Stream):
                contents.append(_stream_bytes(obj))
    for data in contents:
        for label, pattern in _COLOUR_OPERATORS:
            if pattern.search(data):
                found.add(label)
        found |= {_DEVICE_NAMES["/Device" + m.decode()] for m in _DEVICE_SPACE_OPERATOR.findall(data)}
    return found


def output_intent_for(spaces: set[str]) -> str | None:
    """ "rgb" or "cmyk"; None when uncalibrated RGB and CMYK are both painted -- no single intent fits,
    and fixing that means rewriting page content, which this project never does."""
    if "RGB" in spaces and "CMYK" in spaces:
        return None
    return "cmyk" if "CMYK" in spaces else "rgb"


def cid_fonts_without_map(pdf: pikepdf.Pdf) -> int:
    """Embedded CIDFontType2 fonts with no /CIDToGIDMap. PDF/A-2 6.2.11.3.2 requires one, and adding it
    to someone else's font means asserting its CIDs are glyph ids -- a guess about content we don't own."""
    count = 0
    for category, obj in iter_resources(pdf):
        if category != "/Font" or not isinstance(obj, pikepdf.Dictionary) or obj.get("/Subtype") != pikepdf.Name.Type0:
            continue
        for descendant in obj.get("/DescendantFonts") or []:
            if descendant.get("/Subtype") == pikepdf.Name.CIDFontType2 and "/CIDToGIDMap" not in descendant:
                count += 1
    return count


def pdfa_blockers(pdf: pikepdf.Pdf) -> list[str]:
    """Why this file cannot become PDF/A without touching content it doesn't own. Empty means it can.
    Conservative on one point: a non-embedded font counts even if it only draws invisible text,
    which PDF/A would allow."""
    blockers = []
    if any(not embedded for embedded in font_inventory(pdf).values()):
        blockers.append("non-embedded font")
    if output_intent_for(device_colour_spaces(pdf)) is None:
        blockers.append("uncalibrated RGB and CMYK together")
    if cid_fonts_without_map(pdf):
        blockers.append("original font lacks CIDToGIDMap")
    if any(annotation_problems(annot) for annot in annotations(pdf)):
        blockers.append("annotation not printable or without appearance")
    return blockers


_ANNOT_PRINT = 4
_ANNOT_INVISIBLE_HIDDEN_NOVIEW = 1 | 2 | 32


def annotation_problems(annot: Any) -> set[str]:
    """PDF/A-2 6.3.2 / 6.3.3: every annotation but a Popup is printed and shown, and every one but a
    Link carries an appearance stream."""
    subtype = annot.get("/Subtype")
    if subtype == pikepdf.Name.Popup:
        return set()
    problems = set()
    flags = int(annot.get("/F", 0))
    if not flags & _ANNOT_PRINT or flags & _ANNOT_INVISIBLE_HIDDEN_NOVIEW:
        problems.add("annotation not printable or hidden")
    if subtype != pikepdf.Name.Link and "/AP" not in annot:
        problems.add("annotation without appearance")
    return problems


def _is_transparency_group(group: Any) -> bool:
    return isinstance(group, pikepdf.Dictionary) and group.get("/S") == pikepdf.Name.Transparency


def _image_and_transparency_flags(pdf: pikepdf.Pdf) -> dict[str, bool]:
    """Image encodings and colour, and whether anything is transparent: images, groups, graphics states."""
    transparency = alpha = cmyk = jbig2 = ccitt = False
    for category, obj in iter_resources(pdf):
        if category == "/XObject" and isinstance(obj, pikepdf.Stream):
            if obj.get("/Subtype") == pikepdf.Name.Image:
                filters = str(obj.get("/Filter", ""))
                jbig2 = jbig2 or "JBIG2Decode" in filters
                ccitt = ccitt or "CCITTFaxDecode" in filters
                cmyk = cmyk or _is_cmyk(obj.get("/ColorSpace"))
                if obj.get("/SMask") is not None:
                    alpha = transparency = True
            if _is_transparency_group(obj.get("/Group")):
                transparency = True
        if category == "/ExtGState" and isinstance(obj, pikepdf.Dictionary):
            for key in ("/CA", "/ca"):
                if key in obj and float(obj[key]) < 1:
                    transparency = True
            if obj.get("/SMask") not in (None, pikepdf.Name("/None")):
                transparency = True
    if any(_is_transparency_group(page.obj.get("/Group")) for page in pdf.pages):
        transparency = True
    return {"transparency": transparency, "alpha_image": alpha, "cmyk": cmyk, "jbig2": jbig2, "ccitt": ccitt}


def _has_invisible_text(pdf: pikepdf.Pdf) -> bool:
    """Text render mode 3 in any page content stream: an invisible text layer, as prior OCR leaves."""
    return any(
        _INVISIBLE_TEXT_RE.search(_stream_bytes(part))
        for page in pdf.pages
        for part in _stream_parts(page.obj.get("/Contents"))
    )


def census(path: Path) -> dict[str, Any]:
    """Structural facts about one PDF. Counts and flags only -- never text."""
    row: dict[str, Any] = {"error": None}
    try:
        with pikepdf.open(path) as pdf:
            geos = [page_geometry(page) for page in pdf.pages]
            inventory = interactive_inventory(pdf)
            fonts = font_inventory(pdf)
            producer = " ".join(str(pdf.docinfo.get(k, "")) for k in ("/Producer", "/Creator"))
            images = _image_and_transparency_flags(pdf)
            devices = device_colour_spaces(pdf)
            row.update(
                {
                    "pages": len(geos),
                    "device_colour": sorted(devices),
                    "device_cmyk": "CMYK" in devices,
                    "mixed_device_colour": output_intent_for(devices) is None,
                    "cid_font_without_map": cid_fonts_without_map(pdf) > 0,
                    "type3_font": "Type3" in fonts,
                    "pdfa_blockers": pdfa_blockers(pdf),
                    "encrypted": pdf.is_encrypted,
                    "signed": inventory["signatures"] > 0,
                    "acroform": inventory["fields"] > 0,
                    "javascript": has_javascript(pdf),
                    "embedded_files": inventory["attachments"] > 0,
                    "non_embedded_fonts": sum(1 for embedded in fonts.values() if not embedded),
                    "transparency": images["transparency"],
                    "alpha_image": images["alpha_image"],
                    "cmyk": images["cmyk"],
                    "jbig2": images["jbig2"],
                    "ccitt": images["ccitt"],
                    "lzw": has_lzw(pdf),
                    "rotated": any(g["rotate"] for g in geos),
                    "cropbox": any(g["cropbox"] != g["mediabox"] for g in geos),
                    "annotations": inventory["annotations"] > 0,
                    "outline": inventory["outline_items"] > 0,
                    "prior_ocr": bool(OCR_PRODUCER_RE.search(producer)) or _has_invisible_text(pdf),
                    "pdfa_claimed": pdfa_claim(pdf),
                }
            )
    except Exception as exc:
        row["error"] = type(exc).__name__
        return row
    texts = pdftotext_pages(path)
    has_text = [len((texts[i] if i < len(texts) else "").strip()) >= PAGE_TEXT_MIN_CHARS for i in range(row["pages"])]
    row["page_has_text"] = has_text
    row["text_pages"] = sum(has_text)
    row["image_only_pages"] = row["pages"] - row["text_pages"]
    return row


HAZARD_FLAGS = (
    "encrypted",
    "signed",
    "acroform",
    "javascript",
    "embedded_files",
    "non_embedded_fonts",
    "transparency",
    "alpha_image",
    "cmyk",
    "device_cmyk",
    "mixed_device_colour",
    "cid_font_without_map",
    "type3_font",
    "jbig2",
    "ccitt",
    "lzw",
    "rotated",
    "cropbox",
    "annotations",
    "outline",
    "prior_ocr",
    "pdfa_claimed",
)


def hazards_of(row: dict[str, Any]) -> list[str]:
    found = [flag for flag in HAZARD_FLAGS if row.get(flag)]
    if row.get("text_pages") and row.get("image_only_pages"):
        found.append("mixed")
    elif row.get("pages") and not row.get("text_pages"):
        found.append("image_only")
    return found


def triage(row: dict[str, Any]) -> str:
    """refuse: processing cannot avoid breaking something, so the original stands.
    skip: every page already has text -- converted to PDF/A as it is, nothing stamped.
    stamp: text layer on the pages without one, then PDF/A."""
    if row.get("error") or any(row.get(flag) for flag in REFUSE_HAZARDS):
        return "refuse"
    if not row.get("image_only_pages"):
        return "skip"
    return "stamp"


def triage_words(pages: list[list[dict[str, Any]]], row: dict[str, Any]) -> list[list[dict[str, Any]]]:
    """Words for the triage mode: only pages without a text layer, and nothing for refused or skipped files."""
    if triage(row) != "stamp":
        return [[] for _ in pages]
    has_text = row.get("page_has_text", [])
    return [[] if (i < len(has_text) and has_text[i]) else words for i, words in enumerate(pages)]
