"""Master snapshots: the frozen reference every output is compared against, visually and for content.

A master is taken once per input: every page rendered to a greyscale PNG, the text of every page,
and the input's SHA-256. Comparisons read the master rather than the input, so a changed input shows
up as a refreshed master instead of silently moving the reference. For the built-in corpus the input
is itself the source document (or a scan derived from it), so the master is also the ground truth:
the word boxes every lane stamps come from that same source.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from . import config
from .base import read_json, write_json
from .common import pdftotext_pages
from .tools import run_tool

# Fixed before any run (plan, "Metrics", N1 and N2); the judge compares with these.
VERIFY_DPI = 72
PIXEL_DELTA = 64  # grey levels; a pixel that moves more than this has changed
PIXEL_NOISE = 16  # below this a difference is anti-aliasing, not a change


def _gray(image: Path) -> np.ndarray:
    return np.asarray(Image.open(image).convert("L"), dtype=np.int16)


def render_gray(path: Path, tmp: Path, prefix: str) -> list[np.ndarray] | None:
    """Every page as a greyscale array, rendered by poppler -- a renderer none of the lanes use."""
    completed = run_tool(
        ["pdftoppm", "-r", str(VERIFY_DPI), "-gray", str(path), str(tmp / prefix)], config.TOOL_TIMEOUT, text=False
    )
    if completed.returncode != 0:
        return None
    return [_gray(p) for p in sorted(tmp.glob(f"{prefix}-*.pgm"))]


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def snapshot(key: str, path: Path) -> tuple[Path, str]:
    """Take (or reuse) the master for one input. Returns its folder and "new", "kept" or "refreshed"."""
    folder = config.MASTERS / key
    digest = sha256(path)
    meta = folder / "master.json"
    state = "new"
    if meta.exists():
        if read_json(meta).get("sha256") == digest:
            return folder, "kept"
        state = "refreshed"
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("page-*.png"):
        old.unlink()
    with tempfile.TemporaryDirectory() as tmp:
        pages = render_gray(path, Path(tmp), "m") or []
    for index, page in enumerate(pages, 1):
        Image.fromarray(page.clip(0, 255).astype(np.uint8), mode="L").save(folder / f"page-{index:03d}.png")
    write_json(folder / "text.json", {"pages": pdftotext_pages(path)})
    write_json(meta, {"key": key, "source": path.name, "sha256": digest, "pages": len(pages), "dpi": VERIFY_DPI})
    return folder, state


def load_pages(folder: Path | None) -> list[np.ndarray] | None:
    if folder is None or not (folder / "master.json").exists():
        return None
    return [_gray(p) for p in sorted(folder.glob("page-*.png"))]


def load_text(folder: Path | None) -> list[str] | None:
    if folder is None or not (folder / "text.json").exists():
        return None
    return read_json(folder / "text.json")["pages"]


def write_diffs(master: list[np.ndarray], output: list[np.ndarray], folder: Path, stem: str) -> int:
    """One PNG per page that moved: the master in grey, strong changes red, faint ones orange."""
    written = 0
    for index, (a, b) in enumerate(zip(master, output, strict=False), 1):
        h, w = min(a.shape[0], b.shape[0]), min(a.shape[1], b.shape[1])
        diff = np.abs(a[:h, :w] - b[:h, :w])
        if not (diff > PIXEL_NOISE).any():
            continue
        grey = a[:h, :w].clip(0, 255).astype(np.uint8)
        image = np.stack([grey, grey, grey], -1)
        image[diff > PIXEL_NOISE] = [255, 165, 0]
        image[diff > PIXEL_DELTA] = [220, 0, 0]
        folder.mkdir(parents=True, exist_ok=True)
        Image.fromarray(image).save(folder / f"{stem}-p{index:03d}.png")
        written += 1
    return written
