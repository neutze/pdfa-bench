"""One run of the whole experiment.

Build the corpus (once), take a master per input, run every lane in both modes, validate every
output with veraPDF in one batch, check veraPDF against its own test corpus, and write
results.json and per_file.csv. `report.py` turns the result into the terminal tables.
"""

from __future__ import annotations

import csv
import shutil
import time
from pathlib import Path
from typing import Any

from . import config, corpus, judge, lanes, masters, vera
from .base import read_json, say, write_json
from .common import census, hazards_of, read_words, triage, triage_words
from .tools import ToolTimeout, is_timeout, run_tool

CSV_FIELDS = (
    "key",
    "label",
    "lane",
    "mode",
    "triage",
    "produced",
    "failure",
    "brick",
    "brick_reasons",
    "untouched",
    "verapdf",
    "verapdf_rules",
    "readable",
    "position",
    "visual_worst",
    "visual_aligned",
    "images_identical",
    "size_ratio",
    "seconds",
)


def load_corpus() -> list[dict[str, Any]]:
    """The built-in corpus, built on first use and kept, so masters stay valid between runs."""
    folder = config.CORPUS / "synthetic"
    manifest = folder / "manifest.json"
    stale = not manifest.exists() or read_json(manifest).get("version") != corpus.CORPUS_VERSION
    if stale or ((config.SLIDES / "slides-libreoffice.pdf").exists() and not (folder / "S21.pdf").exists()):
        corpus.build(folder)
    return [
        {"key": e["key"], "label": e["label"], "path": folder / e["file"], "words": folder / e["words"]}
        for e in read_json(folder / "manifest.json")["files"]
    ]


def _first_line(command: list[str]) -> str:
    try:
        completed = run_tool(command, config.VERSION_TIMEOUT)
    except (OSError, ToolTimeout):
        return "absent"
    lines = [l for l in (completed.stdout + completed.stderr).splitlines() if l.strip() and not l.startswith("WARNING")]
    return lines[0].strip() if lines else "absent"


def environment() -> dict[str, str]:
    import fitz
    import pikepdf

    jars = sorted(p.name for p in (config.JVM / "lib").glob("pdfbox-3*.jar")) if (config.JVM / "lib").exists() else []
    return {
        "ghostscript": _first_line(["gs", "--version"]) if shutil.which("gs") else "absent",
        "verapdf": _first_line(["verapdf", "--version"]) if shutil.which("verapdf") else "absent",
        "ocrmypdf": _first_line(["ocrmypdf", "--version"]) if shutil.which("ocrmypdf") else "absent",
        "java": _first_line(["java", "-version"]) if shutil.which("java") else "absent",
        "pdfbox": jars[0].removesuffix(".jar") if jars else "absent",
        "pikepdf": pikepdf.__version__,
        "pymupdf": fitz.VersionBind,
        "font": str(config.FONT),
    }


def run(lane_names: list[str] | None = None, modes: tuple[str, ...] = lanes.MODES) -> dict[str, Any]:
    started = time.perf_counter()
    entries = load_corpus()
    rows = {e["key"]: census(e["path"]) for e in entries}
    snaps = {e["key"]: masters.snapshot(e["key"], e["path"]) for e in entries}
    states = [state for _, state in snaps.values()]
    say(
        f"corpus   {len(entries)} files, {sum(r.get('pages', 0) for r in rows.values())} pages; masters "
        f"{states.count('kept')} kept, {states.count('new')} new, {states.count('refreshed')} refreshed"
    )

    names = lane_names or list(lanes.LANES)
    skipped = {name: lanes.lane_missing(name) for name in names if lanes.lane_missing(name)}
    words = {e["key"]: read_words(e["words"]) for e in entries}
    records: list[dict[str, Any]] = []
    for lane in names:
        if lane in skipped:
            say(f"{lane:4} skipped: missing {', '.join(skipped[lane])}")
            continue
        for mode in modes:
            lane_started = time.perf_counter()
            records += [
                _run_one(lane, mode, entry, rows[entry["key"]], words[entry["key"]], snaps[entry["key"]][0])
                for entry in entries
            ]
            say(f"{lane:4} {mode:6} done in {time.perf_counter() - lane_started:5.1f}s")

    outputs = [Path(r["output"]) for r in records if r["output"]]
    say(f"veraPDF  validating {len(outputs)} outputs")
    verdicts = vera.validate(outputs)
    for record in records:
        if record["output"]:
            record.update(verdicts[record["output"]])
    say("veraPDF  checking itself against its own test corpus")
    check = vera.corpus_check()

    results = {
        "seconds": round(time.perf_counter() - started, 1),
        "environment": environment(),
        "lanes": {name: lanes.LANES[name].meta() for name in names},
        "skipped": skipped,
        "verapdf_corpus": check,
        "records": records,
    }
    write_json(config.OUT / "results.json", results)
    write_csv(config.OUT / "per_file.csv", records)
    return results


def _run_one(
    lane: str,
    mode: str,
    entry: dict[str, Any],
    row: dict[str, Any],
    all_words: list[list[dict[str, Any]]],
    master: Path,
) -> dict[str, Any]:
    """One lane on one file in one mode, judged against its master; veraPDF is left to the batch."""
    verdict = triage(row)
    words = all_words if mode == "naive" else triage_words(all_words, row)
    out = config.OUTPUTS / f"{lane}.{mode}" / f"{entry['key']}.pdf"
    t0 = time.perf_counter()
    if mode == "triage" and verdict == "refuse":
        out.unlink(missing_ok=True)
        failure, produced = None, None  # risky file: the original stands
    else:
        failure = lanes.run_lane(lane, entry["path"], words, out)
        produced = out if failure is None else None
    seconds = time.perf_counter() - t0
    try:
        checks = judge.verify(
            entry["path"], produced, words, row, master=master, diff_dir=config.DIFFS / f"{lane}.{mode}", validate=False
        )
    except ToolTimeout as exc:  # the output could not be judged: recorded like a lane failure, and removed
        out.unlink(missing_ok=True)
        failure, produced = str(exc), None
        checks = judge.verify(entry["path"], None, words, row)
    if is_timeout(failure):  # a hang is worth a line of its own; other failures show in the tables
        say(f"{lane:4} {mode:6} {entry['key']}: {failure}")
    return {
        "lane": lane,
        "mode": mode,
        "key": entry["key"],
        "label": entry["label"],
        "failure": failure,
        "seconds": round(seconds, 3),
        "triage": verdict,
        "hazards": hazards_of(row),
        "conformable": not row.get("pdfa_blockers"),
        "blockers": row.get("pdfa_blockers", []),
        "output": str(produced) if produced else None,
        **checks,
    }


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    **record,
                    "produced": bool(record["output"]),
                    "brick_reasons": " ".join(record.get("brick_reasons", [])),
                    "verapdf_rules": " ".join(record.get("verapdf_rules") or []),
                }
            )
