# IDEAS

- A second validator (e.g. the PDFBox preflight for PDF/A-1b, or a commercial one where licensed) to
  cross-check veraPDF on the lanes' outputs, not only on its own corpus.
- A `strip-and-restamp` lane for scans carrying a garbage prior OCR layer (OCRmyPDF's `--mode strip`),
  which triage currently skips.
- A PDF/A-3b variant, which permits arbitrary attachments and would un-refuse the attachment case.
- Publish the per-file matrix as an HTML report with the diff images inline.
