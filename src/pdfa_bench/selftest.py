"""Self-test: the machinery, not the hypotheses.

The census finds every planted hazard, the verification gate catches damage it is shown, the
stamping code puts words where they belong on rotated and cropped pages, the PDF/A recipe passes
veraPDF where veraPDF is present, and the decision rule ranks lanes as the test plan fixed it.
Runs in a temporary folder, removed when every check passes and kept for inspection when one
fails; the image runs it at build time.

    python -m pdfa_bench selftest
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRATCH = Path(tempfile.mkdtemp(prefix="pdfa-selftest-"))
os.environ["PDFA_BENCH_OUT"] = str(SCRATCH / "out")

import fitz  # noqa: E402
import pikepdf  # noqa: E402

from . import bench, config, decision, judge, lanes, report, vera  # noqa: E402
from . import corpus as pdfa_corpus  # noqa: E402
from .common import (  # noqa: E402
    census,
    displayed_to_user,
    hazards_of,
    read_words,
    triage,
    triage_words,
    user_to_displayed,
    words_tsv,
)
from .tools import ToolTimeout, is_timeout, run_tool  # noqa: E402

results: list[tuple[str, bool]] = []


def check(name: str, condition: bool) -> None:
    results.append((name, bool(condition)))


def main() -> None:
    tools = Path(__file__).resolve().parent
    entries = pdfa_corpus.build(config.CORPUS / "synthetic")
    base = config.CORPUS / "synthetic"
    rows = {e["key"]: census(base / e["file"]) for e in entries}
    words = {e["key"]: read_words(base / e["words"]) for e in entries}
    path = {e["key"]: base / e["file"] for e in entries}

    # --- Corpus and census --------------------------------------------------------------
    check("corpus has the 20 constructed files", sum(1 for e in entries if e["key"] <= "S20") == 20)
    if (config.SLIDES / "slides-libreoffice.pdf").exists():
        check("slide decks are in the corpus", {"S21", "S22", "S23"} <= {e["key"] for e in entries})
    check(
        "the 12-page scan has 12 image-only pages", rows["S18"]["pages"] == 12 and rows["S18"]["image_only_pages"] == 12
    )
    check("every synthetic file opens", all(not row["error"] for row in rows.values()))
    for entry in entries:
        found = set(hazards_of(rows[entry["key"]]))
        check(f"census finds planted hazards in {entry['key']}", set(entry["intended"]) <= found)
    check("plain letter carries no refuse hazard", triage(rows["S01"]) == "skip")
    check("scan is stamped", triage(rows["S03"]) == "stamp")
    check(
        "forms, encryption, JavaScript and attachments are refused",
        all(triage(rows[k]) == "refuse" for k in ("S08", "S10", "S15", "S16")),
    )
    mixed = triage_words(words["S12"], rows["S12"])
    check("triage stamps only the page without text", not mixed[0] and mixed[1])
    check("triage stamps nothing on a refused file", not any(triage_words(words["S08"], rows["S08"])))

    # --- Geometry ---------------------------------------------------------------------------
    for rotate in (0, 90, 180, 270):
        geo = {"mediabox": [0, 0, 700, 900], "cropbox": [30, 40, 625, 882], "rotate": rotate}
        u, v = user_to_displayed(*displayed_to_user(0.2, 0.7, geo), geo)
        check(f"displayed/user round trip at /Rotate {rotate}", abs(u - 0.2) < 1e-9 and abs(v - 0.7) < 1e-9)
    upright = {"mediabox": [0, 0, 595, 842], "cropbox": [0, 0, 595, 842], "rotate": 0}
    check("top-left displayed is top-left of the page", displayed_to_user(0, 0, upright) == (0, 842))
    tsv_rows = words_tsv(words["S03"]).strip().splitlines()
    check("Kotlin word file has six columns per row", tsv_rows and all(len(r.split("\t")) == 6 for r in tsv_rows))

    # --- Verification gate: controls ------------------------------------------------------------
    work = SCRATCH / "gate"
    work.mkdir()
    copy = work / "copy.pdf"
    shutil.copyfile(path["S09"], copy)
    same = judge.verify(path["S09"], copy, [[]], rows["S09"])
    check("byte copy is untouched and no brick", same["untouched"] and not same["brick"] and same["visual_worst"] == 0)
    check("nothing produced is not a brick", not judge.verify(path["S03"], None, words["S03"], rows["S03"])["brick"])

    # --- Verification gate: it must catch damage -----------------------------------------------------
    raster = work / "raster.pdf"
    lanes.rasterise(path["S01"], [[]], raster)
    check(
        "rasterised born-digital loses its fonts",
        "fonts lost" in judge.verify(path["S01"], raster, [[]], rows["S01"])["brick_reasons"],
    )

    no_field = work / "nofield.pdf"
    with pikepdf.open(path["S08"]) as pdf:
        del pdf.Root.AcroForm.Fields[0]
        pdf.save(no_field)
    check(
        "a dropped form field is caught",
        "interactive lost" in judge.verify(path["S08"], no_field, [[]], rows["S08"])["brick_reasons"],
    )

    one_page = work / "onepage.pdf"
    with pikepdf.open(path["S12"]) as pdf:
        del pdf.pages[1]
        pdf.save(one_page)
    check(
        "a dropped page is caught",
        "page count" in judge.verify(path["S12"], one_page, [[], []], rows["S12"])["brick_reasons"],
    )

    blank = work / "blank.pdf"
    with pikepdf.open(path["S01"]) as pdf:
        pdf.pages[0].obj.Contents = pdf.make_stream(b"")
        pdf.save(blank)
    blank_reasons = judge.verify(path["S01"], blank, [[]], rows["S01"])["brick_reasons"]
    check(
        "an emptied page is caught as visual and text loss", "visual" in blank_reasons and "text lost" in blank_reasons
    )

    truncated = work / "truncated.pdf"
    truncated.write_bytes(path["S01"].read_bytes()[:2000])
    check(
        "a truncated file is unopenable",
        judge.verify(path["S01"], truncated, [[]], rows["S01"])["brick_reasons"] == ["unopenable"],
    )

    # --- Stamping puts words where they belong ----------------------------------------------------
    # Rotation and an offset CropBox together broke placement once; S14 at every /Rotate covers it.
    for rotate in (90, 180, 270):
        key = f"S14r{rotate}"
        path[key] = work / f"{key}.pdf"
        with pikepdf.open(path["S14"]) as pdf:
            pdf.pages[0].obj.Rotate = rotate
            pdf.save(path[key])
        rows[key], words[key] = census(path[key]), words["S14"]
    for lane in ("P3", "P4", "P5", "P6"):
        for key in ("S03", "S13", "S14", "S14r90", "S14r180", "S14r270"):
            out = work / f"{lane}-{key}.pdf"
            failure = lanes.run_lane(lane, path[key], words[key], out)
            record = judge.verify(path[key], out if failure is None else None, words[key], rows[key])
            check(f"{lane} on {key}: produced", failure is None and record["produced"])
            check(f"{lane} on {key}: stamped text is readable", record.get("readable") is True)
            check(f"{lane} on {key}: words land in their boxes (>= 0.8)", (record.get("position") or 0) >= 0.8)
    incremental = work / "P4-S03.pdf"
    check(
        "incremental save keeps the original bytes as prefix",
        incremental.read_bytes().startswith(path["S03"].read_bytes()),
    )

    gs_out = work / "P2-S03.pdf"
    gs_failure = lanes.run_lane("P2", path["S03"], words["S03"], gs_out)
    check(
        "Ghostscript lane produces a file or records why not, never a partial file",
        (gs_failure is None and gs_out.exists()) or (gs_failure is not None and not gs_out.exists()),
    )

    # --- PDF/A recipe: intent by colour, Info/XMP agreement, CID map, Ghostscript intent -------------
    check(
        "uncalibrated CMYK scan is seen as DeviceCMYK",
        rows["S19"]["device_cmyk"] and not rows["S19"]["mixed_device_colour"],
    )
    check("RGB plus DeviceCMYK on one page is not conformable", rows["S20"]["mixed_device_colour"])
    cmyk_out, mixed_out = work / "P3-S19.pdf", work / "P3-S20.pdf"
    lanes.run_lane("P3", path["S19"], words["S19"], cmyk_out)
    lanes.run_lane("P3", path["S20"], words["S20"], mixed_out)
    check("CMYK-only scan gets a CMYK output intent", judge.structure(cmyk_out)["intent_channels"] == 4)
    mixed = judge.structure(mixed_out)
    check(
        "not-conformable file is stamped but not marked PDF/A", not mixed["pdfa_claim"] and not mixed["output_intent"]
    )
    p4 = judge.structure(work / "P4-S03.pdf")
    check("PyMuPDF lane writes XMP that agrees with Info", p4["docinfo_keys"] and not p4["info_xmp_mismatch"])
    p3 = judge.structure(work / "P3-S03.pdf")
    check("pikepdf lane writes XMP that agrees with Info", not p3["info_xmp_mismatch"])
    with pikepdf.open(work / "P3-S03.pdf") as pdf:
        cid = [
            o
            for o in pdf.objects
            if isinstance(o, pikepdf.Dictionary) and o.get("/Subtype") == pikepdf.Name.CIDFontType2
        ]
        check("text-layer CID font carries /CIDToGIDMap", cid and all("/CIDToGIDMap" in o for o in cid))
    gs_struct = judge.structure(gs_out) if gs_out.exists() else None
    check("Ghostscript output carries an output intent", gs_struct is not None and gs_struct["intent_channels"] == 3)

    squeezed = work / "compressed-metadata.pdf"
    # qpdf (pikepdf's writer) always rewrites /Metadata uncompressed, whatever the save options, so the
    # broken file is made with PyMuPDF, which compresses one stream on request.
    with fitz.open(work / "P3-S03.pdf") as doc:
        kind, ref = doc.xref_get_key(doc.pdf_catalog(), "Metadata")
        xref = int(ref.split()[0])
        doc.update_stream(xref, doc.xref_stream(xref), compress=True)
        doc.save(squeezed)
    check(
        "pre-check catches compressed metadata",
        "compressed metadata" in judge.precheck(judge.structure(squeezed))["precheck_problems"],
    )
    drifted = work / "info-drift.pdf"
    with pikepdf.open(work / "P4-S03.pdf") as pdf:
        pdf.docinfo["/Producer"] = "changed after marking"
        pdf.save(drifted)
    check(
        "pre-check catches Info/XMP drift",
        "info/xmp mismatch" in judge.precheck(judge.structure(drifted))["precheck_problems"],
    )

    if vera.command():
        unmarked = vera.validate_one(path["S03"])
        check(
            "veraPDF: unmarked scan is not PDF/A and names a rule",
            unmarked["verapdf"] is False and unmarked["verapdf_rules"],
        )
        for lane in ("P3", "P4", "P5"):
            check(
                f"veraPDF: {lane} stamped scan is PDF/A-2b",
                vera.validate_one(work / f"{lane}-S03.pdf")["verapdf"] is True,
            )
        check("veraPDF: CMYK-intent scan is PDF/A-2b", vera.validate_one(cmyk_out)["verapdf"] is True)
        check(
            "veraPDF: Ghostscript output is PDF/A-2b", gs_out.exists() and vera.validate_one(gs_out)["verapdf"] is True
        )

    # --- Files that already have text: PDF/A as they are, nothing stamped -----------------------------
    marked = work / "P3-S01-markonly.pdf"
    lanes.run_lane("P3", path["S01"], [[]], marked)
    record = judge.verify(path["S01"], marked, [[]], rows["S01"])
    check("born-digital letter is conformable once PyMuPDF's font map is set", not rows["S01"]["pdfa_blockers"])
    check("PDF/A-only conversion leaves a text file untouched", record["untouched"] and not record["brick"])
    check("PDF/A-only conversion marks PDF/A", bool(judge.structure(marked)["pdfa_claim"]))
    check("non-embedded Base-14 font blocks PDF/A", "non-embedded font" in rows["S02"]["pdfa_blockers"])
    if vera.command():
        check("veraPDF: PDF/A-only conversion of a text file is PDF/A-2b", vera.validate_one(marked)["verapdf"] is True)
    check("unprintable annotations block PDF/A", any("annotation" in b for b in rows["S09"]["pdfa_blockers"]))
    if "S23" in path and vera.command():
        deck = work / "P3-S23.pdf"
        lanes.run_lane("P3", path["S23"], words["S23"], deck)
        check(
            "veraPDF: slides with bullets, arrows and emoji stamp to PDF/A-2b (no .notdef glyphs)",
            vera.validate_one(deck)["verapdf"] is True,
        )

    # --- Time limits: a hang is a failure for that one file ------------------------------------------
    try:
        run_tool(["sleep", "5"], 0.2)
        check("a tool past its time limit is stopped", False)
    except ToolTimeout as exc:
        check("a tool past its time limit is stopped", str(exc) == "sleep timed out after 0.2s")
    limit = config.TOOL_TIMEOUT
    config.TOOL_TIMEOUT = 0.001
    try:
        if not lanes.lane_missing("P2"):
            hung = work / "P2-hung.pdf"
            note = lanes.run_lane("P2", path["S03"], words["S03"], hung)
            check(
                "a lane whose tool hangs fails with a timeout note and leaves no output",
                is_timeout(note) and note.startswith("gs ") and not hung.exists(),
            )
        entry = {"key": "S01", "label": "timeout check", "path": path["S01"]}
        record = bench._run_one("P0", "naive", entry, rows["S01"], words["S01"], None)
        check(
            "an output whose check hangs is recorded as a failure, not a verdict",
            is_timeout(record["failure"]) and record["output"] is None and not record["brick"],
        )
    finally:
        config.TOOL_TIMEOUT = limit

    # --- veraPDF failing is said, not hidden ----------------------------------------------------------
    real_command = vera.command
    try:
        vera.command = lambda: ["false"]  # exits 1 with no report
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            silent = vera.validate([path["S01"]])[str(path["S01"])]
        check(
            "an unreadable veraPDF report is warned about and leaves the file not checked",
            silent["verapdf"] is None and "no readable report" in said.getvalue(),
        )
        if real_command():
            vera.command = lambda: ["sh", "-c", 'verapdf "$@"; exit 9', "verapdf"]  # a report, then an odd exit
            said = io.StringIO()
            with contextlib.redirect_stdout(said):
                odd = vera.validate([path["S01"]])[str(path["S01"])]
            check(
                "an unexpected veraPDF exit is warned about, and the verdicts it did report are kept",
                odd["verapdf"] is not None and "exit 9" in said.getvalue(),
            )
            said = io.StringIO()
            with contextlib.redirect_stdout(said):
                vera.command = real_command
                vera.validate([path["S01"], truncated])
            check("veraPDF failing to parse a broken output is a verdict, not a warning", not said.getvalue())
    finally:
        vera.command = real_command

    # --- Decision rule, on made-up triage records -----------------------------------------------------
    decision_rule()

    # --- Gates: nothing is fetched, gated lanes stay gated -------------------------------------------
    if lanes.lane_missing("K1"):
        check("Kotlin lanes report what is missing", bool(lanes.lane_missing("K1")) and bool(lanes.lane_missing("K1i")))
    else:
        for lane in ("K1", "K1i"):
            for key in ("S03", "S14r90", "S19"):
                out = work / f"{lane}-{key}.pdf"
                record = judge.verify(
                    path[key],
                    out if lanes.run_lane(lane, path[key], words[key], out) is None else None,
                    words[key],
                    rows[key],
                )
                check(
                    f"{lane} on {key}: readable, placed, not bricked",
                    record["produced"]
                    and record.get("readable") is True
                    and (record.get("position") or 0) >= 0.8
                    and not record["brick"],
                )
        if vera.command():
            check(
                "veraPDF: Kotlin stamp on a CMYK scan is PDF/A-2b",
                vera.validate_one(work / "K1-S19.pdf")["verapdf"] is True,
            )
    check(
        "veraPDF absent means conformance not checked",
        vera.command() is not None or vera.validate_one(path["S01"])["verapdf"] is None,
    )
    for option, value in (("--lanes", "P3,P9"), ("--modes", "triage,triag")):
        refused = subprocess.run(
            [sys.executable, "-m", "pdfa_bench", "run", option, value], capture_output=True, text=True
        )
        check(
            f"{option} rejects an unknown value before running anything",
            refused.returncode != 0 and "unknown" in refused.stderr and not (config.OUT / "results.json").exists(),
        )
    fetchers = re.compile(
        r"""\[\s*["'](curl|wget|pip3?|mvn|gradle|brew|uv|huggingface-cli)["']|import (urllib|requests|socket)|from (urllib|requests)"""
    )
    sources = sorted(p for p in tools.glob("*.py") if p.name != "selftest.py")
    check(
        "no PDF/A harness script can download anything",
        not any(fetchers.search(p.read_text(encoding="utf-8")) for p in sources),
    )

    for script in sources + [tools / "selftest.py"]:
        compiled = subprocess.run([sys.executable, "-m", "py_compile", str(script)], capture_output=True)
        check(f"compiles: {script.name}", compiled.returncode == 0)

    failed = [name for name, passed in results if not passed]
    for name, passed in results:
        print(f"{'ok  ' if passed else 'FAIL'} {name}")
    if failed:  # the scratch folder stays for a look at what failed
        print(f"pdfa selftest: {len(results) - len(failed)}/{len(results)} checks passed; scratch kept: {SCRATCH}")
        sys.exit(1)
    shutil.rmtree(SCRATCH, ignore_errors=True)
    print(f"pdfa selftest: {len(results)}/{len(results)} checks passed")


def _record(
    lane: str,
    key: str,
    *,
    pdfa: bool = True,
    brick: bool = False,
    output: bool = True,
    verdict: str = "stamp",
    position: float = 1.0,
    untouched: bool = True,
    seconds: float = 0.1,
    failure: str | None = None,
) -> dict:
    return {
        "lane": lane,
        "mode": "triage",
        "key": key,
        "label": key,
        "failure": failure,
        "output": f"{lane}-{key}.pdf" if output else None,
        "brick": brick,
        "brick_reasons": ["visual"] if brick else [],
        "verapdf": pdfa if output else None,
        "untouched": untouched,
        "triage": verdict,
        "position": position,
        "seconds": seconds,
        "conformable": True,
    }


def decision_rule() -> None:
    """decision.rank: bricks make a lane unsafe; safe lanes rank by PDF/A passes over what triage processes,
    then position, untouched outputs and speed; a gap under two files is inconclusive; only ranked lanes rank."""
    keys = ("F1", "F2", "F3", "F4")
    lanes_meta = {name: {"label": name, "role": "ranked"} for name in ("A", "B", "C", "D", "E", "G")}
    lanes_meta["P0"] = {"label": "control", "role": "control"}
    records = (
        [_record("A", k) for k in keys]  # 4/4 PDF/A
        + [_record("B", k, pdfa=k == "F1") for k in keys]  # 1/4
        + [_record("C", k, brick=k == "F2") for k in keys]  # one brick: unsafe
        + [_record("D", k, output=k != "F3") for k in keys]  # nothing produced for F3: a fail
        + [_record("E", k, position=0.5) for k in keys]  # A's passes, worse position
        + [_record("G", k, output=k != "F4", verdict="refuse" if k == "F4" else "stamp") for k in keys]  # F4 refused
        + [_record("P0", k, brick=True) for k in keys]  # control: never ranked or judged
    )
    groups = decision.group_by_lane_mode(records)
    verdict = decision.rank(groups, lanes_meta)
    check("decision: a lane that bricks any file in triage is unsafe", verdict["unsafe"] == ["C"])
    check(
        "decision: control, reference and comparison lanes are never ranked",
        "P0" not in verdict["safe"] + verdict["unsafe"] + verdict["ranking"],
    )
    check("decision: a refused file is not a target", decision.aggregate(groups[("G", "triage")])["targets"] == 3)
    # A and E tie on PDF/A passes, A places words better; D (3 of 4, nothing for F3) and G (3 of 3 targets)
    # tie on everything and fall back to name; B trails.
    check(
        "decision: PDF/A passes, then word position, rank the safe lanes",
        verdict["ranking"] == ["A", "E", "D", "G", "B"],
    )
    check("decision: a gap under two files is inconclusive", any(n.startswith("A vs E:") for n in verdict["notes"]))
    check("decision: a gap of two files is conclusive", not any(n.startswith("G vs B:") for n in verdict["notes"]))
    cells = [
        report._cell(_record("X", "F", failure="gs exit 1")),
        report._cell(_record("X", "F", output=False)),
        report._cell(_record("X", "F", brick=True)),
        report._cell(_record("X", "F")),
        report._cell(_record("X", "F", untouched=False)),
        report._cell(_record("X", "F", pdfa=False)),
    ]
    check("per-file matrix symbols follow the legend", cells == ["!", "·", "✗", "✓", "✓~", "○"])


if __name__ == "__main__":
    main()
