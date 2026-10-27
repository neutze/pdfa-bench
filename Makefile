# Local gate. The image runs the same self-test at build time.
PY ?= python3

.PHONY: check gate lint selftest jvm slides run image

gate:  ## the gate, as CI runs it: ruff, gitleaks, the image build with its self-test
	./scripts/ci.sh

check: lint selftest  ## lint + self-test, with the tools installed on this machine (quicker, not the gate)

lint:
	uvx ruff check --select F,E9 src slides

selftest: jvm slides
	PYTHONPATH=src $(PY) -m pdfa_bench selftest

jvm: .jvm/pdfa-stamper.jar

.jvm/pdfa-stamper.jar: jvm/PdfaStamper.kt versions.env scripts/fetch-jvm.sh
	./scripts/fetch-jvm.sh .jvm

slides: .cache/slides/slides-chromium.pdf

.cache/slides/slides-chromium.pdf: slides/make_slides.py slides/requirements.txt
	uv run --no-project --with-requirements slides/requirements.txt python slides/make_slides.py .cache/slides

run:  ## the one command
	./pdfa-bench

image:
	./pdfa-bench --rebuild list
