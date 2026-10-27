# pdfa-bench

**Can you add an OCR text layer to a PDF and mark it PDF/A without breaking the file?**
pdfa-bench measures that, in one command, for eleven different ways of doing it — and prints the
answer as tables in your terminal.

## In plain words

A scanned page becomes searchable when an invisible layer of text is laid over it, and a file
becomes fit for long-term keeping when it is marked PDF/A, the ISO format archives ask for. The
usual recipe does both by letting Ghostscript rewrite the whole file. That rewrite is what quietly
breaks files: scans are re-encoded, the page shifts by a pixel, form fields or attachments go
missing, and twice during development Ghostscript reported success for a file that was not PDF/A
at all.

pdfa-bench gives eleven methods the same 23 test files, whose correct look and text are known
exactly, and checks every output against the original: does each page still look the same, is the
text still there and readable, is anything inside the file lost, and does veraPDF, the reference
validator, accept it. In the v0.2.0 run, the Ghostscript recipe broke 13 of the 19 files it was
given; the four methods that only add the text on top of the existing page broke none.

No documents of yours are needed or read: the test files are generated, plus veraPDF's own public
test corpus. If you only want the answer, it is [the recipe that works](#the-recipe-that-works).

```bash
./pdfa-bench
```

The first start builds a container image and fetches everything it needs (Ghostscript, veraPDF,
PDFBox, Kotlin, the Python libraries, veraPDF's own test corpus), every version pinned in
[`versions.env`](versions.env). Later starts run in a few minutes. Needs **Podman or Docker**; on a Mac
the launcher starts the Podman machine if it isn't running.

## Why

Adding a text layer is easy. Keeping the file intact while doing it — images, fonts, form fields,
signatures, the page as it looks — and producing a file a validator accepts as PDF/A is not. The
usual recipe (stamp the text, then let Ghostscript rewrite the whole file as PDF/A) is exactly the
pipeline suspected of breaking files, and without a validator it can fail silently: during
development Ghostscript twice exited "successfully" and wrote a file that was not PDF/A at all.

## What it runs

Every lane gets the same file and the same word boxes, so the method is the only variable.

| Lane | Method |
|---|---|
| P0 | control — the original, copied byte for byte |
| P1 | text layer with pikepdf, then Ghostscript PDF/A-2b, defaults |
| P2 | the same, Ghostscript told to pass images through and never auto-rotate |
| P2r | P2, Ghostscript told to abort rather than drop what cannot conform |
| **P3** | pikepdf: text layer as an overlay form XObject, PDF/A markers, existing streams untouched |
| **P4** | PyMuPDF: the same overlay, saved incrementally (the original bytes stay the file's prefix) |
| P5 | PyMuPDF: full rewrite with garbage collection and content cleaning |
| P6 | reference — rasterise every page, invisible text over the image (what "force OCR" does) |
| P7 | comparison — OCRmyPDF 17 `--mode skip --output-type pdfa`, what Paperless-ngx runs |
| **K1 / K1i** | Kotlin + PDFBox 3, full save / incremental save |

Each lane runs in two modes: **naive** stamps every page of every file (it shows what breaks);
**triage** is what a real service would ship — a text layer on pages without one, files that already
have text converted to PDF/A as they are, and risky files (encrypted, signed, forms, JavaScript,
attachments) left untouched.

## The test data

Built in, no documents of yours involved:

- **20 constructed files**, each carrying one structural hazard: embedded and non-embedded fonts,
  RGB / CMYK / 1-bit CCITT / alpha-channel scans, uncalibrated DeviceCMYK, RGB and CMYK mixed on one
  page, transparency, form fields, annotations, links and an outline, AES-256 encryption, a garbage
  prior OCR layer, mixed text and scanned pages, `/Rotate 90`, an offset CropBox, JavaScript, an
  attachment, an existing PDF/A claim, a 12-page scan. Every file is drawn from a known source, so
  the **master** — what the page must look like and what text it must contain — is known exactly.
- **3 slide decks**, exported the way people make them: a PowerPoint deck (gradients, a photo with
  transparency, a translucent overlay, rotated text, a chart, a table, symbols and emoji) exported by
  **LibreOffice**; a web deck (CSS gradients, opacity, shadows, blend modes, SVG, emoji) printed by
  **Chromium** — the Skia engine Google Slides exports with; and the same deck saved as images.
- **veraPDF's test corpus** (PDF/A-2b part, CC BY 4.0), whose files state their expected verdict in
  their names. pdfa-bench runs veraPDF over it first: if veraPDF disagrees with its own corpus, no
  PDF/A verdict in the results is trusted.

## How an output is judged

Against its **master**, taken once per input and frozen under `results/masters/`:

- **Visually** — every page rendered by poppler (a renderer no lane uses) and compared pixel by
  pixel. Any page that moved gets a diff image in `results/diff/` (red: changed, orange: faint).
- **Content** — the original text layer must survive; the stamped words must be readable by
  `pdftotext` (what Paperless reads) and sit where they belong on the page.
- **Structure** — fonts and their embedding, image bytes, form fields, annotations, outline,
  attachments, signatures.
- **PDF/A** — veraPDF, PDF/A-2b profile.

An output is **bricked** if it can't be opened, loses or gains pages, has a page with more than 0.5%
of its pixels visibly changed, loses text, fonts, form fields, annotations or attachments, or
invalidates a signature. These thresholds were fixed before any result existed.

## Reading the output

```
TRIAGE — text layer on scans, text files converted as they are, risky files refused (what a service would ship)
Lane  Method                                            Output  Bricked  Untouched  PDF/A-2b  of conformable
P1    pikepdf stamp, then Ghostscript PDF/A (defaults)   19/23       13          0     19/19           14/14
P3    pikepdf overlay + PDF/A markers                    19/23        0         19     14/19           14/14
P4    PyMuPDF overlay, incremental save                  19/23        0         19     14/19           14/14
P7    comparison: OCRmyPDF (what Paperless runs)         19/23        3          6     17/19           13/14
K1i   Kotlin + PDFBox, incremental save                  19/23        0         19     14/19           14/14
...
SLIDES (triage)
  P1   S21 ✗   S22 ✓~   S23 ✓~
  K1i  S21 ✓   S22 ✓    S23 ✓
DECISION (rule fixed before any run)
  safe in triage:   P3, P4, K1, K1i
  unsafe (bricked): P1, P2, P2r, P5
veraPDF self-check: 986/986 test-corpus files match their expected verdict (trustworthy)
```

(An excerpt of the v0.2.0 run: Ghostscript 10.08.0, veraPDF 1.30.2, PDFBox 3.0.8. "Of conformable"
counts only files whose original can become PDF/A without touching its content.)

Then a per-file matrix (`✓` PDF/A and untouched, `✓~` PDF/A but bytes changed, `○` safe but not
PDF/A, `✗` bricked, `·` not stamped), why files broke, and the decision:

1. a lane that bricks any file in triage mode is **unsafe**;
2. safe lanes rank by PDF/A-2b passes over the files triage stamps, then word position, then
   untouched outputs, then speed;
3. a gap of fewer than two files is inconclusive.

Everything is also written to `results/results.json` and `results/per_file.csv`.

## The recipe that works

What the safe lanes do, and what a service should copy:

- add the text **on top of** each page — an appended content stream or form XObject in PDF user
  space, never a re-rendered page and never an "overlay page" helper (pikepdf's and PyMuPDF's both
  misplace the layer on rotated or cropped pages);
- choose the **output intent from the colours the file actually paints with** — sRGB, CMYK, or
  none: a page painting uncalibrated RGB *and* CMYK cannot be PDF/A without rewriting it, so it is
  stamped (searchable) but not marked PDF/A;
- keep the **XMP metadata in sync** with the Info dictionary;
- give the text layer's CID font a `/CIDToGIDMap` (PyMuPDF omits it; veraPDF rule 6.2.11.3.2);
- drop characters the text-layer font cannot draw (bullets, arrows, emoji) — a `.notdef` glyph fails
  PDF/A (rule 6.2.11.8);
- convert files that already have text **as they are** — never stamp a second layer on top;
- don't claim PDF/A when the original's own fonts can't conform;
- then validate with veraPDF and compare with the original — and keep the original on any failure.

## Running outside the container

The harness also runs directly on a Mac with Homebrew's `ghostscript`, `poppler`, `qpdf` and
`verapdf`, and Python with the packages in `requirements.txt`:

```bash
make check                                   # lint + self-test
PYTHONPATH=src python3 -m pdfa_bench run     # results in ./results
```

The container is the reference environment: font and ICC profiles differ between the two.

## Where it came from

I built it to decide how my own document pipeline should make its archival PDFs, after an earlier
attempt with the usual recipe had damaged files and left no record of how. The hypotheses, the
thresholds and the decision rule above were written down before the first run, so the results could
not move them ([DECISIONS.md](DECISIONS.md)). The story of what it found, and what the benchmark
itself got wrong first, is on [neutze.de](https://neutze.de).

## Licence

MIT for this repository. The image bundles third-party software and data under their own licences —
see [NOTICE](NOTICE).
