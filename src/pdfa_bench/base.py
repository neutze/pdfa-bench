"""Small shared helpers: JSON files, text normalisation, and the one way to print."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

_MARKUP_RES = [
    re.compile(r"<[^>]+>"),
    re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE),
    re.compile(r"\*\*|__"),
]
_WS_RE = re.compile(r"\s+")
WORD_RE = re.compile(r"\w+(?:[.,:/\-]\w+)*", re.UNICODE)


def normalize(text: str) -> str:
    """NFC, markup removed, whitespace collapsed. Case, punctuation and diacritics kept."""
    value = unicodedata.normalize("NFC", text or "")
    for pattern in _MARKUP_RES:
        value = pattern.sub(" ", value)
    return _WS_RE.sub(" ", value.replace("|", " ")).strip()


def tokens(text: str) -> list[str]:
    return WORD_RE.findall(normalize(text))


def read_json(path: Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=_jsonable), encoding="utf-8")
    tmp.replace(path)


def _jsonable(value: Any) -> Any:
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def say(message: str = "") -> None:
    print(message, flush=True)
