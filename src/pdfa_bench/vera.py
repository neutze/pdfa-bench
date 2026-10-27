"""veraPDF: PDF/A-2b conformance for every output, and a check that veraPDF itself is right.

veraPDF runs as one JVM per batch of files, not one per file. `corpus_check` runs it over a pinned
subset of the public veraPDF test corpus (CC BY 4.0), whose file names state the expected verdict
("-pass-" or "-fail-"): if veraPDF disagrees with those, no PDF/A verdict in the results can be trusted.
"""

from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from . import config
from .base import say
from .tools import ToolTimeout, run_tool

BATCH = 100
FLAVOUR = "2b"
# veraPDF's exit codes that are verdicts on the files (org.verapdf.cli.CliConstants.ExitCodes, 1.30): all
# conform, some don't, some failed to parse, some encrypted. Any other code is veraPDF itself failing.
VERDICT_EXITS = (0, 1, 7, 8)


def command() -> list[str] | None:
    found = shutil.which("verapdf")
    return [found] if found else None


def validate(paths: list[Path]) -> dict[str, dict[str, Any]]:
    """{str(path): {"verapdf": True/False/None, "verapdf_rules": ["clause-test", ...]}} for every path."""
    results: dict[str, dict[str, Any]] = {str(p): {"verapdf": None, "verapdf_rules": []} for p in paths}
    cmd = command()
    if cmd is None or not paths:
        return results
    for start in range(0, len(paths), BATCH):
        chunk = [str(p) for p in paths[start : start + BATCH]]
        try:
            completed = run_tool(cmd + ["--flavour", FLAVOUR, "--format", "mrr", *chunk], config.VERAPDF_TIMEOUT)
        except ToolTimeout as exc:
            say(f"veraPDF  warning: {exc}; {len(chunk)} files stay not checked")
            continue
        try:
            root = ET.fromstring(completed.stdout)
        except ET.ParseError:
            say(
                f"veraPDF  warning: exit {completed.returncode} and no readable report; {len(chunk)} files stay not checked"
            )
            continue
        _read_report(root, results)
        if completed.returncode not in VERDICT_EXITS:
            unchecked = sum(1 for p in chunk if results[p]["verapdf"] is None)
            say(f"veraPDF  warning: exit {completed.returncode}; {unchecked} of {len(chunk)} files have no verdict")
    return results


def _read_report(root: ET.Element, results: dict[str, dict[str, Any]]) -> None:
    """Fill in the verdict of every job in a machine-readable report whose file is one of `results`."""
    for job in root.iter("job"):
        name = job.findtext("item/name") or ""
        report = job.find("validationReport")
        if report is None:
            continue
        failed = sorted(
            {f"{r.get('clause')}-{r.get('testNumber')}" for r in report.iter("rule") if r.get("status") == "failed"}
        )
        match = (
            name if name in results else next((k for k in results if Path(k).resolve() == Path(name).resolve()), None)
        )
        if match:
            results[match] = {"verapdf": report.get("isCompliant") == "true", "verapdf_rules": failed}


def validate_one(path: Path) -> dict[str, Any]:
    return validate([path])[str(path)]


def corpus_check() -> dict[str, Any]:
    """Agreement between veraPDF and the corpus's own expected verdicts."""
    folder = config.VERAPDF_CORPUS
    files = sorted(p for p in folder.rglob("*.pdf")) if folder.exists() else []
    labelled = [(p, "-pass-" in p.name) for p in files if "-pass-" in p.name or "-fail-" in p.name]
    if not labelled or command() is None:
        return {"files": 0, "agree": 0, "disagree": [], "available": False}
    verdicts = validate([p for p, _ in labelled])
    disagree = [p.name for p, expected in labelled if verdicts[str(p)]["verapdf"] is not expected]
    return {"files": len(labelled), "agree": len(labelled) - len(disagree), "disagree": disagree, "available": True}
