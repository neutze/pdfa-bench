# Decisions

What was decided, when and why, newest last. A threshold or rule changes only with an entry here.

## 2026-09-15 — the test plan, before any run

- **The question:** can a service put a searchable text layer into a PDF and mark it PDF/A without
  breaking the file? It is two promises, measured apart: *never damage a file* (can be guaranteed by
  construction plus a check) and *every output is PDF/A* (cannot, without rewriting page content). A
  safe method must be allowed to answer "left as it was".
- **Bricked** means: cannot be opened, pages gained or lost, a page with more than 0.5% of its pixels
  visibly changed (72 dpi, greyscale), text, fonts, form fields, annotations or attachments lost, or a
  signature invalidated. Fixed before any result; the raw numbers are kept so another threshold can be
  applied afterwards and reported as such.
- **The decision rule:** a lane that bricks any file in triage mode is unsafe; control, reference and
  comparison lanes are reported, never ranked; safe lanes rank by PDF/A-2b passes over the files triage
  processes, then word position, then untouched outputs, then speed; a gap under two files is
  inconclusive.
- **No real documents.** The corpus is constructed, so every master is known exactly.
- **Known limits, kept:** the visual check is greyscale and cannot see a colour shift at equal
  brightness; triage trusts any text layer, so a scan with garbage prior OCR is skipped, not re-stamped;
  a font renamed but kept intact counts as lost.

## 2026-09-25 — the harness becomes its own tool

- veraPDF is the only PDF/A judge, and is checked against its own public corpus first; PDFBox and Kotlin
  are fetched for the K1/K1i lanes. Every external artifact is pinned by version and SHA-256 in
  `versions.env`.
- One command (`./pdfa-bench`) builds a pinned image and runs everything; the image's last build step is
  the self-test.

## 2026-09-27 — v0.1.0 reproduced from a fresh clone

- The synthetic result held exactly on a no-cache rebuild. Recipe fixes found while getting veraPDF
  green (output intent from device colours, XMP synced from Info, `/CIDToGIDMap` on PyMuPDF's font,
  Ghostscript's ICC passed as hex and its def file by absolute path, no PDF/A claim when a blocker
  exists) are in `CLAUDE.md`.

## 2026-10-27 — its own repository

- The bench left the document pipeline's repository for `pdfa-bench-private`, published as `pdfa-bench`
  through release commits only (owner, 2026-10-26: "bench own repo"). The first commit carries the tree
  of v0.2.0, not the earlier history.
- A fresh run of v0.2.0 on 2026-10-26 matched the README's results exactly (veraPDF 986/986 on its own
  corpus).
