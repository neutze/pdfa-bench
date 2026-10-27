"""Terminal report: two summary tables, a per-file matrix, why files broke, and the decision.

Only prints: the counting and the decision rule are in `decision.py`.
"""

from __future__ import annotations

import os
from collections import Counter
from typing import Any

from . import config
from .base import say
from .decision import Groups, aggregate, group_by_lane_mode, rank
from .judge import ALIGN_PX, VISUAL_BRICK

CONFORMABLE = (
    "of conformable: outputs whose original can become PDF/A without touching its content — not when the "
    "original has a non-embedded font, a CID font without CIDToGIDMap, or uncalibrated RGB and CMYK on one page"
)
LEGEND = (
    "✓ PDF/A-2b and untouched   ✓~ PDF/A-2b, bytes or pixels changed (not a brick)   "
    "○ safe, not PDF/A   ✗ bricked   · refused: a risky file left as it was   ! lane failed"
)

SUMMARY_HEADER = [
    "Lane",
    "Method",
    "Output",
    "Bricked",
    "Untouched",
    "PDF/A-2b",
    "of conformable",
    "Readable",
    "Position",
    "Size ×",
    "s/file",
]
SUMMARY_TITLES = (
    (
        "triage",
        "TRIAGE — text layer on scans, text files converted as they are, risky files refused "
        "(what a service would ship)",
    ),
    ("naive", "NAIVE — stamp every page of every file (shows what breaks)"),
)


def _table(title: str, header: list[str], rows: list[list[Any]]) -> None:
    """Left-aligned first two columns (key and label), right-aligned numbers."""
    widths = [max(len(str(c)) for c in column) for column in zip(header, *rows, strict=False)]

    def line(cells: list[Any]) -> str:
        return "  ".join(
            str(c).ljust(w) if i < 2 else str(c).rjust(w) for i, (c, w) in enumerate(zip(cells, widths, strict=False))
        )

    say()
    say(title)
    say(line(header))
    say("  ".join("─" * w for w in widths))
    for row in rows:
        say(line(row))


def _cell(record: dict[str, Any]) -> str:
    if record["failure"]:
        return "!"
    if not record["output"]:
        return "·"
    if record["brick"]:
        return "✗"
    if record.get("verapdf") is True:
        return "✓" if record.get("untouched") else "✓~"
    return "○"


def show(results: dict[str, Any]) -> None:
    records = results["records"]
    lanes = results["lanes"]
    groups = group_by_lane_mode(records)
    _heading(results["environment"])
    _summaries(groups, lanes)
    _per_file(records, groups, lanes)
    _why_files_broke(records, groups)
    _decision(rank(groups, lanes), lanes)
    _slides(records, lanes)
    _footer(results)


def _heading(env: dict[str, str]) -> None:
    say()
    say("pdfa-bench — adding an invisible text layer and marking PDF/A-2b, without breaking the file")
    say(
        f"Ghostscript {env['ghostscript']} · veraPDF {env['verapdf'].removeprefix('veraPDF ')} · {env['pdfbox']} · "
        f"pikepdf {env['pikepdf']} · PyMuPDF {env['pymupdf']} · OCRmyPDF {env['ocrmypdf']}"
    )


def _or_dash(value: Any) -> Any:
    return value if value is not None else "-"


def _summaries(groups: Groups, lanes: dict[str, dict[str, str]]) -> None:
    for mode, title in SUMMARY_TITLES:
        rows = []
        for lane, meta in lanes.items():
            group = groups.get((lane, mode))
            if not group:
                continue
            agg = aggregate(group)
            pdfa = f"{agg['pdfa']}/{agg['stamped']}" if agg["checked"] else "not checked"
            conformable = f"{agg['pdfa_conformable']}/{agg['conformable']}" if agg["checked"] else "-"
            rows.append(
                [
                    lane,
                    meta["label"],
                    f"{agg['stamped']}/{agg['files']}",
                    agg["bricks"],
                    agg["untouched"],
                    pdfa,
                    conformable,
                    agg["readable"],
                    _or_dash(agg["position"]),
                    _or_dash(agg["size"]),
                    _or_dash(agg["seconds"]),
                ]
            )
        _table(title, SUMMARY_HEADER, rows)


def _per_file(records: list[dict[str, Any]], groups: Groups, lanes: dict[str, dict[str, str]]) -> None:
    lane_order = [lane for lane in lanes if (lane, "triage") in groups]
    labels = {}
    for record in records:
        labels.setdefault(record["key"], record["label"])
    by = {(r["lane"], r["mode"], r["key"]): r for r in records}
    rows = [
        [key, labels[key][:44]] + [_cell(by[(lane, "triage", key)]) for lane in lane_order] for key in sorted(labels)
    ]
    say(CONFORMABLE)
    _table("PER FILE, triage mode", ["File", "What it tests"] + lane_order, rows)
    say(LEGEND)


def _why_files_broke(records: list[dict[str, Any]], groups: Groups) -> None:
    bricked: dict[str, Counter] = {}
    for (lane, mode), group in groups.items():
        for record in group:
            if record["brick"]:
                bricked.setdefault(f"{lane} {mode}", Counter()).update(record["brick_reasons"])
    if not bricked:
        return
    say()
    say("WHY FILES BROKE")
    for name, reasons in bricked.items():
        say(f"  {name:10} " + ", ".join(f"{reason} ×{count}" for reason, count in reasons.most_common()))
    resampled = [
        r
        for r in records
        if r["brick"] and r["brick_reasons"] == ["visual"] and (r.get("visual_aligned") or 0) <= VISUAL_BRICK
    ]
    if resampled:
        say(
            f"  {len(resampled)} of the visual bricks shrink below the threshold after a ±{ALIGN_PX} px shift: "
            "pages resampled or shifted, text still legible — see diff/"
        )


def _decision(decision: dict[str, Any], lanes: dict[str, dict[str, str]]) -> None:
    say()
    say("DECISION (rule fixed before any run)")
    say(f"  safe in triage:   {', '.join(decision['safe']) or 'none'}")
    say(f"  unsafe (bricked): {', '.join(decision['unsafe']) or 'none'}")
    say(f"  ranking:          {' > '.join(decision['ranking']) or '-'}")
    for note in decision["notes"]:
        say(f"  note: {note}")
    if decision["ranking"]:
        best = decision["ranking"][0]
        say(f"  use:              {best} — {lanes[best]['label']}")
        kotlin = next((lane for lane in decision["ranking"] if lane.startswith("K")), None)
        if kotlin:
            say(f"  on the JVM:       {kotlin} — {lanes[kotlin]['label']}")


def _slides(records: list[dict[str, Any]], lanes: dict[str, dict[str, str]]) -> None:
    slides = [r for r in records if r["mode"] == "triage" and r["label"].startswith("slides")]
    if not slides:
        return
    say()
    say("SLIDES (triage)")
    for lane in lanes:
        cells = [f"{r['key']} {_cell(r)}" for r in slides if r["lane"] == lane]
        if cells:
            say(f"  {lane:4} " + "   ".join(cells))


def _footer(results: dict[str, Any]) -> None:
    check = results["verapdf_corpus"]
    say()
    if check["available"]:
        verdict = "trustworthy" if not check["disagree"] else "DISAGREES — treat PDF/A verdicts with caution"
        say(
            f"veraPDF self-check: {check['agree']}/{check['files']} test-corpus files match their expected verdict ({verdict})"
        )
    else:
        say("veraPDF self-check: not run (veraPDF or its test corpus missing)")
    for lane, missing in results["skipped"].items():
        say(f"skipped {lane}: missing {', '.join(missing)}")
    base = os.environ.get("PDFA_BENCH_RESULTS_HOST") or str(config.OUT)  # the launcher passes the host folder in
    say(f"Details in {base}: results.json · per_file.csv · diff/ (pages that moved) · masters/ · {results['seconds']}s")
