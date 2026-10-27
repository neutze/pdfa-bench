# syntax=docker/dockerfile:1
# pdfa-bench image. Every external artifact is pinned by version and SHA-256 in versions.env;
# ./pdfa-bench passes them in as build args. Building this image is the one moment anything is downloaded.

# --- Ghostscript, built from the official source: Debian's package predates 10.07.0, and
#     10.05-10.06 corrupt PDF/A output (dropped ligatures, truncated JPEG passthrough).
FROM debian:trixie-slim AS ghostscript
ARG GS_VERSION
ARG GS_TAG
ARG GS_SHA256
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential ca-certificates curl pkg-config \
 && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL -o /tmp/gs.tar.gz "https://github.com/ArtifexSoftware/ghostpdl-downloads/releases/download/${GS_TAG}/ghostscript-${GS_VERSION}.tar.gz" \
 && echo "${GS_SHA256}  /tmp/gs.tar.gz" | sha256sum -c - \
 && tar -xzf /tmp/gs.tar.gz -C /tmp \
 && cd "/tmp/ghostscript-${GS_VERSION}" \
 && ./configure --prefix=/opt/gs --without-x --disable-cups --disable-dbus --disable-gtk --disable-fontconfig --without-tesseract \
 && make -j"$(nproc)" \
 && make install \
 && /opt/gs/bin/gs --version
# The build compiles its ICC profiles into the binary; the other lanes need them as files.
RUN cp -r "/tmp/ghostscript-${GS_VERSION}/iccprofiles" /opt/gs/share/ghostscript/iccprofiles \
 && test -f /opt/gs/share/ghostscript/iccprofiles/srgb.icc \
 && test -f /opt/gs/share/ghostscript/iccprofiles/default_cmyk.icc

# --- JVM side: PDFBox + the Kotlin stamper (lanes K1/K1i), and veraPDF installed headlessly.
FROM eclipse-temurin:21-jdk AS jvm
ARG VERAPDF_VERSION
ARG VERAPDF_SHA256
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl unzip \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY versions.env ./
COPY jvm ./jvm
COPY scripts/fetch-jvm.sh ./scripts/
RUN ./scripts/fetch-jvm.sh /opt/pdfa-bench/jvm && rm -rf /opt/pdfa-bench/jvm/kotlin
RUN curl -fsSL -o /tmp/verapdf.zip "https://software.verapdf.org/rel/${VERAPDF_VERSION%.*}/verapdf-greenfield-${VERAPDF_VERSION}-installer.zip" \
 && echo "${VERAPDF_SHA256}  /tmp/verapdf.zip" | sha256sum -c - \
 && unzip -q /tmp/verapdf.zip -d /tmp \
 && java -DINSTALL_PATH=/opt/verapdf -jar "/tmp/verapdf-greenfield-${VERAPDF_VERSION}/verapdf-izpack-installer-${VERAPDF_VERSION}.jar" -options-system \
 && /opt/verapdf/verapdf --version

# --- veraPDF's own test corpus (CC BY 4.0), PDF/A-2b part only, at a pinned commit.
FROM debian:trixie-slim AS verapdf-corpus
ARG VERAPDF_CORPUS_COMMIT
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git \
 && rm -rf /var/lib/apt/lists/*
RUN git init -q /corpus \
 && cd /corpus \
 && git remote add origin https://github.com/veraPDF/veraPDF-corpus.git \
 && git sparse-checkout set "PDF_A-2b" \
 && git fetch -q --depth 1 --filter=blob:none origin "${VERAPDF_CORPUS_COMMIT}" \
 && git checkout -q FETCH_HEAD \
 && test -d PDF_A-2b

# --- Slide decks, exported the way people make them: PowerPoint via LibreOffice, a web deck via Chromium.
FROM debian:trixie-slim AS slides
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates libreoffice-impress chromium \
      fonts-dejavu-core fonts-liberation2 fonts-crosextra-carlito fonts-noto-color-emoji python3-venv \
 && rm -rf /var/lib/apt/lists/*
COPY slides/requirements.txt slides/make_slides.py /tmp/slides/
RUN python3 -m venv /venv \
 && /venv/bin/pip install --no-cache-dir -r /tmp/slides/requirements.txt \
 && /venv/bin/python /tmp/slides/make_slides.py /slides

# --- Runtime.
FROM python:3.13-slim-trixie
RUN apt-get update \
 && apt-get install -y --no-install-recommends poppler-utils qpdf fonts-dejavu-core \
      tesseract-ocr tesseract-ocr-deu tesseract-ocr-eng \
 && rm -rf /var/lib/apt/lists/*
COPY --from=eclipse-temurin:21-jre /opt/java/openjdk /opt/java/openjdk
COPY --from=ghostscript /opt/gs /opt/gs
COPY --from=jvm /opt/pdfa-bench/jvm /opt/pdfa-bench/jvm
COPY --from=jvm /opt/verapdf /opt/verapdf
COPY --from=verapdf-corpus /corpus/PDF_A-2b /opt/pdfa-bench/verapdf-corpus/PDF_A-2b
COPY --from=slides /slides /opt/pdfa-bench/slides
ENV JAVA_HOME=/opt/java/openjdk \
    PATH=/opt/gs/bin:/opt/verapdf:/opt/java/openjdk/bin:$PATH \
    PYTHONPATH=/opt/pdfa-bench/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PDFA_BENCH_OUT=/out
COPY requirements.txt /opt/pdfa-bench/
RUN pip install --no-cache-dir -r /opt/pdfa-bench/requirements.txt
COPY src /opt/pdfa-bench/src
# A broken image never ships: the self-test (every lane, veraPDF included) is the last build step.
RUN if python -m pdfa_bench selftest > /tmp/selftest.log 2>&1; then tail -1 /tmp/selftest.log; \
    else grep -v '^ok ' /tmp/selftest.log; exit 1; fi
WORKDIR /out
ENTRYPOINT ["python", "-m", "pdfa_bench"]
CMD ["run"]
