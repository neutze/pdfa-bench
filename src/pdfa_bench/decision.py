"""The decision: which lanes are safe, and how the safe ones rank. Applied mechanically to the records.

Decision rule (fixed in the test plan before any result existed):
  1. A lane that bricks any file in triage mode is unsafe. Triage is what a service would ship.
  2. Control (P0), reference (P6) and comparison (P7) lanes are reported, never ranked.
  3. Safe lanes rank by PDF/A-2b passes over the files triage processes (stamps, or converts as they
     are when they already have text) -- a file the lane produced nothing for counts as a fail -- then
     by word position, then untouched outputs, then seconds.
  4. A gap of fewer than 2 files is inconclusive.
"""

from __future__ import annotations

import statistics
from typing import Any

Groups = dict[tuple[str, str], list[dict[str, Any]]]

INCONCLUSIVE_GAP = 2  # rule 4: PDF/A passes two ranked lanes must differ by to be told apart


def group_by_lane_mode(records: list[dict[str, Any]]) -> Groups:
    groups: Groups = {}
    for record in records:
        groups.setdefault((record["lane"], record["mode"]), []).append(record)
    return groups


def aggregate(records: list[dict[str, Any]]) -> dict[str, Any]:
    produced = [r for r in records if r["output"]]
    targets = [r for r in records if r["triage"] != "refuse"] if records and records[0]["mode"] == "triage" else records
    positions = [r["position"] for r in produced if r.get("position") is not None]
    ratios = [r["size_ratio"] for r in produced if r.get("size_ratio") is not None]
    seconds = [r["seconds"] for r in produced]
    readable = [r["readable"] for r in produced if r.get("readable") is not None]
    return {
        "files": len(records),
        "stamped": len(produced),
        "failed": sum(1 for r in records if r["failure"]),
        "bricks": sum(1 for r in records if r["brick"]),
        "untouched": sum(1 for r in produced if r.get("untouched")),
        "pdfa": sum(1 for r in produced if r.get("verapdf") is True),
        "conformable": sum(1 for r in produced if r.get("conformable")),
        "pdfa_conformable": sum(1 for r in produced if r.get("conformable") and r.get("verapdf") is True),
        "pdfa_targets": sum(1 for r in targets if r.get("verapdf") is True),
        "targets": len(targets),
        "checked": any(r.get("verapdf") is not None for r in produced),
        "readable": f"{sum(readable)}/{len(readable)}" if readable else "-",
        "position": round(statistics.mean(positions), 2) if positions else None,
        "size": round(statistics.median(ratios), 2) if ratios else None,
        "seconds": round(statistics.median(seconds), 2) if seconds else None,
    }


def rank(groups: Groups, lanes: dict[str, dict[str, str]]) -> dict[str, Any]:
    safe, unsafe, scored = [], [], []
    for lane, meta in lanes.items():
        records = groups.get((lane, "triage"))
        if not records or meta["role"] != "ranked":
            continue
        agg = aggregate(records)
        if agg["bricks"]:
            unsafe.append(lane)
            continue
        safe.append(lane)
        scored.append(
            (-agg["pdfa_targets"], -(agg["position"] or 0), -agg["untouched"], agg["seconds"] or 0, lane, agg)
        )
    scored.sort()
    order = [item[4] for item in scored]
    notes = []
    for first, second in zip(scored, scored[1:], strict=False):
        if abs(first[0] - second[0]) < INCONCLUSIVE_GAP:
            notes.append(f"{first[4]} vs {second[4]}: PDF/A gap under {INCONCLUSIVE_GAP} files, inconclusive")
    return {"safe": safe, "unsafe": unsafe, "ranking": order, "notes": notes}
