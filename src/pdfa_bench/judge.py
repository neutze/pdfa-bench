"""Did the file survive? Compare a lane's output with the input's master.

`verify()` checks pages, pixels, images, fonts, text, form fields, annotations, attachments and
signatures -- and whether the stamped words can be read back where they were put. `structure()` and
`precheck()` are the PDF/A-2b preconditions checkable without veraPDF. The thresholds are research
0003's, fixed before any result existed.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import pikepdf

from . import config, masters, vera
from .common import (
    annotation_problems,
    annotations,
    bbox_words,
    device_colour_spaces,
    font_inventory,
    has_javascript,
    has_lzw,
    image_hashes,
    interactive_inventory,
    pdfa_claim,
    pdftotext_pages,
    token_counter,
    token_recall,
)
from .masters import PIXEL_DELTA, PIXEL_NOISE, render_gray
from .tools import ToolTimeout, run_tool

# Fixed before any run (plan, "Metrics", N1-N5).
VISUAL_BRICK = 0.005  # worst page with more than 0.5% changed pixels is visibly damaged
TEXT_KEEP = 0.98  # the original text layer must survive at this token recall
READABLE_RECALL = 0.90  # stamped words pdftotext must find again (what Paperless's OCR_MODE=auto reads)
POSITION_MARGIN = 0.25  # a found word's centre may sit this far (share of box size) outside its box
POSITION_MIN_CHARS = 3  # shorter words are not scored for position
DOUBLED_TEXT_RATIO = 1.6  # more tokens than this times the original's: a second text layer went on top
# How the checks apply them; not part of the plan's rule.
TEXT_KEEP_MIN_TOKENS = 20  # text recall is judged only when the original has at least this many tokens
POSITION_SLACK = 0.005  # added to the margin (share of the page), so tiny boxes are not judged to the pixel
PAGE_SIZE_SLACK_PX = 2  # rendered pages differing by more than this in either dimension changed size
ALIGN_PX = 2  # diagnostic only: re-measure after the best shift within this many pixels

# Info key -> (XMP property, whether values must match as text). Dates are compared by presence.
INFO_XMP = {
    "/Title": ("dc:title", True),
    "/Author": ("dc:creator", True),
    "/Subject": ("dc:description", True),
    "/Keywords": ("pdf:Keywords", True),
    "/Creator": ("xmp:CreatorTool", True),
    "/Producer": ("pdf:Producer", True),
    "/CreationDate": ("xmp:CreateDate", False),
    "/ModDate": ("xmp:ModifyDate", False),
}


def visual_change(original: list[np.ndarray], output: list[np.ndarray]) -> tuple[float, float, float]:
    """(worst page share of pixels moved > PIXEL_DELTA, worst share moved at all, worst share after
    the best +-ALIGN_PX shift). The first decides the brick rule; the third only tells a resampled or
    shifted page from destroyed content. Ghostscript re-encoding a scan moved every glyph edge by about
    a pixel (measured 2026-09-25), which the rule counts as a brick while the text stays intact."""
    worst = worst_any = worst_aligned = 0.0
    for a, b in zip(original, output, strict=False):
        if abs(a.shape[0] - b.shape[0]) > PAGE_SIZE_SLACK_PX or abs(a.shape[1] - b.shape[1]) > PAGE_SIZE_SLACK_PX:
            return 1.0, 1.0, 1.0
        h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
        a, b = a[:h, :w], b[:h, :w]
        diff = np.abs(a - b)
        worst = max(worst, float((diff > PIXEL_DELTA).mean()))
        worst_any = max(worst_any, float((diff > PIXEL_NOISE).mean()))
        m = ALIGN_PX
        inner = a[m : h - m, m : w - m]
        aligned = min(
            float((np.abs(inner - b[m + dy : h - m + dy, m + dx : w - m + dx]) > PIXEL_DELTA).mean())
            for dy in range(-m, m + 1)
            for dx in range(-m, m + 1)
        )
        worst_aligned = max(worst_aligned, aligned)
    return worst, worst_any, worst_aligned


def signatures_valid(path: Path) -> int:
    completed = run_tool(["pdfsig", str(path)], config.TOOL_TIMEOUT)
    return completed.stdout.count("Signature is Valid")


def qpdf_ok(path: Path) -> bool:
    return run_tool(["qpdf", "--check", str(path)], config.TOOL_TIMEOUT, text=False).returncode in (0, 3)


def structure(path: Path) -> dict[str, Any]:
    with pikepdf.open(path) as pdf:
        fonts = font_inventory(pdf)
        return {
            "pages": len(pdf.pages),
            "encrypted": pdf.is_encrypted,
            "fonts": fonts,
            "images": image_hashes(pdf),
            "interactive": interactive_inventory(pdf),
            "javascript": has_javascript(pdf),
            "pdfa_claim": pdfa_claim(pdf),
            "output_intent": "/OutputIntents" in pdf.Root,
            "docinfo_keys": sorted(str(k) for k in pdf.docinfo.keys()),
            "intent_channels": _intent_channels(pdf),
            "device_colour": device_colour_spaces(pdf),
            "metadata_filter": "/Metadata" in pdf.Root and "/Filter" in pdf.Root.Metadata,
            "info_xmp_mismatch": info_xmp_mismatches(pdf),
            "annotation_problems": sorted(set().union(*(annotation_problems(a) for a in annotations(pdf)))),
            "need_appearances": bool(pdf.Root.AcroForm.get("/NeedAppearances", False))
            if "/AcroForm" in pdf.Root
            else False,
            "lzw": has_lzw(pdf),
        }


def _intent_channels(pdf: pikepdf.Pdf) -> int | None:
    intents = pdf.Root.get("/OutputIntents")
    if not intents:
        return None
    profile = intents[0].get("/DestOutputProfile")
    return int(profile.get("/N", 0)) if profile is not None else None


def info_xmp_mismatches(pdf: pikepdf.Pdf) -> list[str]:
    """Info entries with no equal XMP counterpart -- PDF/A-2 requires the two to agree."""
    if not pdf.docinfo:
        return []
    try:
        meta = pdf.open_metadata()
    except Exception:
        return ["unreadable xmp"]
    problems = []
    for key, (prop, compare) in INFO_XMP.items():
        if key not in pdf.docinfo:
            continue
        value = meta.get(prop)
        if isinstance(value, (list, tuple)) and len(value) == 1:
            value = value[0]
        if value in (None, "", []):
            problems.append(f"{key} missing in xmp")
        elif compare and str(value) != str(pdf.docinfo[key]):
            problems.append(f"{key} differs")
    return problems


def precheck(s: dict[str, Any]) -> dict[str, Any]:
    """PDF/A-2b preconditions checkable without veraPDF. Passing this is NOT conformance."""
    problems = []
    if not s["pdfa_claim"]:
        problems.append("no pdfaid")
    if not s["output_intent"]:
        problems.append("no output intent")
    if s["encrypted"]:
        problems.append("encrypted")
    if s["javascript"]:
        problems.append("javascript")
    if s["interactive"]["attachments"]:
        problems.append("attachments")
    if any(not embedded for embedded in s["fonts"].values()):
        problems.append("non-embedded font")
    if s["lzw"]:
        problems.append("lzw")
    if s["metadata_filter"]:
        problems.append("compressed metadata")
    if s["info_xmp_mismatch"]:
        problems.append("info/xmp mismatch")
    channels, colour = s["intent_channels"], s["device_colour"]
    if (
        (channels == 3 and "CMYK" in colour)
        or (channels == 4 and "RGB" in colour)
        or (channels is None and colour - {"Gray"})
    ):
        problems.append("intent does not match device colour")
    problems += s["annotation_problems"]
    if s["need_appearances"]:
        problems.append("NeedAppearances")
    return {"precheck_pass": not problems, "precheck_problems": problems}


def position_accuracy(
    words: list[list[dict[str, Any]]], found: list[list[dict[str, Any]]], pages: set[int]
) -> float | None:
    hits = total = 0
    for index in pages:
        candidates = found[index] if index < len(found) else []
        for word in words[index] if index < len(words) else []:
            text = word["text"].strip()
            if len(text) < POSITION_MIN_CHARS:
                continue
            total += 1
            x0, y0, x1, y1 = word["box"]
            mx, my = (x1 - x0) * POSITION_MARGIN + POSITION_SLACK, (y1 - y0) * POSITION_MARGIN + POSITION_SLACK
            for other in candidates:
                if other["text"] != text:
                    continue
                cx = (other["box"][0] + other["box"][2]) / 2
                cy = (other["box"][1] + other["box"][3]) / 2
                if x0 - mx <= cx <= x1 + mx and y0 - my <= cy <= y1 + my:
                    hits += 1
                    break
    return hits / total if total else None


def verify(
    original: Path,
    output: Path | None,
    words: list[list[dict[str, Any]]],
    row: dict[str, Any],
    master: Path | None = None,
    diff_dir: Path | None = None,
    validate: bool = True,
) -> dict[str, Any]:
    """Compare an output with the input's master. `words` are what the lane was asked to stamp.
    Without a master the input is rendered afresh; with `validate=False` veraPDF is left to a batch."""
    result: dict[str, Any] = {"produced": output is not None and output.exists()}
    if not result["produced"]:
        result.update({"brick": False, "brick_reasons": [], "untouched": True})
        return result
    reasons: list[str] = []
    before = structure(original)
    try:
        after = structure(output)
        opens = qpdf_ok(output)
    except ToolTimeout:
        raise  # not a verdict on the file: the caller records it as a failure
    except Exception:
        after, opens = None, False
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        rendered_before = masters.load_pages(master) or render_gray(original, tmp_path, "a")
        rendered_after = render_gray(output, tmp_path, "b") if after else None
    if not opens or after is None or rendered_after is None:
        result.update({"brick": True, "brick_reasons": ["unopenable"], "untouched": False})
        return result
    if after["pages"] != before["pages"]:
        reasons.append("page count")
    visual, visual_any, visual_aligned = visual_change(rendered_before or [], rendered_after)
    diffs = (
        masters.write_diffs(rendered_before or [], rendered_after, diff_dir, output.stem)
        if diff_dir and visual_any
        else 0
    )
    if visual > VISUAL_BRICK:
        reasons.append("visual")

    text_before, text_after = masters.load_text(master) or pdftotext_pages(original), pdftotext_pages(output)
    counter_before, counter_after = token_counter("\n".join(text_before)), token_counter("\n".join(text_after))
    kept = token_recall(counter_before, counter_after)
    if kept is not None and sum(counter_before.values()) >= TEXT_KEEP_MIN_TOKENS and kept < TEXT_KEEP:
        reasons.append("text lost")

    fonts_lost = [
        name
        for name, embedded in before["fonts"].items()
        if name not in after["fonts"] or (embedded and not after["fonts"][name])
    ]
    if fonts_lost:
        reasons.append("fonts lost")
    lost = {
        key: before["interactive"][key] - after["interactive"].get(key, 0)
        for key in before["interactive"]
        if after["interactive"].get(key, 0) < before["interactive"][key]
    }
    if lost:
        reasons.append("interactive lost")
    signed_before = signatures_valid(original) if row.get("signed") else 0
    if signed_before and signatures_valid(output) < signed_before:
        reasons.append("signature invalidated")

    stamped_pages = {i for i, page_words in enumerate(words) if page_words}
    image_only = {i for i in stamped_pages if not (row.get("page_has_text") or [False] * (i + 1))[i]}
    stamped_counter = token_counter("\n".join(w["text"] for i in stamped_pages for w in words[i]))
    readable = token_recall(stamped_counter, counter_after)
    doubled = None
    if stamped_pages - image_only:
        base = sum(counter_before.values())
        doubled = base > 0 and sum(counter_after.values()) > DOUBLED_TEXT_RATIO * base

    images_before = before["images"]
    kept_images = sum(min(n, after["images"][h]) for h, n in images_before.items())
    images_identical = kept_images / sum(images_before.values()) if images_before else None

    result.update(
        {
            "brick": bool(reasons),
            "brick_reasons": reasons,
            "untouched": visual_any == 0 and (images_identical in (None, 1.0)) and not fonts_lost and not lost,
            "visual_worst": round(visual, 5),
            "visual_any": round(visual_any, 5),
            "visual_aligned": round(visual_aligned, 5),
            "diff_images": diffs,
            "text_kept": None if kept is None else round(kept, 4),
            "fonts_lost": len(fonts_lost),
            "interactive_lost": lost,
            "images_identical": None if images_identical is None else round(images_identical, 4),
            "encryption_removed": before["encrypted"] and not after["encrypted"],
            "javascript_removed": before["javascript"] and not after["javascript"],
            "readable_recall": None if readable is None else round(readable, 4),
            "readable": None if readable is None else readable >= READABLE_RECALL,
            "doubled_text_layer": doubled,
            "position": position_accuracy(words, bbox_words(output), image_only) if image_only else None,
            "size_ratio": round(output.stat().st_size / max(original.stat().st_size, 1), 3),
            **precheck(after),
            **(vera.validate_one(output) if validate else {"verapdf": None, "verapdf_rules": []}),
        }
    )
    return result
