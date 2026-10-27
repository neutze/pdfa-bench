# TODO

- [ ] Publish the image to GHCR so the first start pulls instead of building (Ghostscript compiles for minutes).
- [ ] Validate in-process with the veraPDF Java library instead of the CLI, one JVM for the whole run.
- [ ] Measure a private corpus through the same lanes without committing it: an optional mounted folder,
      opaque keys in the output (removed from v0.1 on purpose — the built-in corpus is the product).
- [ ] Signed files: the corpus has none (making one needs a signing library); add a self-signed case to test
      whether incremental saves (P4, K1i) keep signatures valid.
- [ ] JBIG2 images: no encoder in the image yet.
