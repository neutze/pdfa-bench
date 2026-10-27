#!/usr/bin/env bash
# Fetch PDFBox (pinned, checksummed) and build the Kotlin stamper jar into DEST.
# Used by the Dockerfile and by `make jvm`. Uses kotlinc from PATH, or fetches the pinned compiler.
set -euo pipefail
dest=${1:?usage: fetch-jvm.sh DEST}
root=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck source=/dev/null
. "$root/versions.env"

sha256_ok() {  # file checksum
  if command -v sha256sum >/dev/null; then echo "$2  $1" | sha256sum -c --quiet -; else echo "$2  $1" | shasum -a 256 -c --quiet -; fi
}
fetch() {  # url checksum out
  if [ -f "$3" ] && sha256_ok "$3" "$2" 2>/dev/null; then return; fi
  curl -fsSL "$1" -o "$3.part"
  sha256_ok "$3.part" "$2" || { echo "checksum mismatch: $1" >&2; rm -f "$3.part"; exit 1; }
  mv "$3.part" "$3"
}

maven=https://repo1.maven.org/maven2
mkdir -p "$dest/lib"
fetch "$maven/org/apache/pdfbox/pdfbox/$PDFBOX_VERSION/pdfbox-$PDFBOX_VERSION.jar" "$PDFBOX_SHA256" "$dest/lib/pdfbox-$PDFBOX_VERSION.jar"
fetch "$maven/org/apache/pdfbox/pdfbox-io/$PDFBOX_VERSION/pdfbox-io-$PDFBOX_VERSION.jar" "$PDFBOX_IO_SHA256" "$dest/lib/pdfbox-io-$PDFBOX_VERSION.jar"
fetch "$maven/org/apache/pdfbox/fontbox/$PDFBOX_VERSION/fontbox-$PDFBOX_VERSION.jar" "$FONTBOX_SHA256" "$dest/lib/fontbox-$PDFBOX_VERSION.jar"
fetch "$maven/org/apache/pdfbox/xmpbox/$PDFBOX_VERSION/xmpbox-$PDFBOX_VERSION.jar" "$XMPBOX_SHA256" "$dest/lib/xmpbox-$PDFBOX_VERSION.jar"
fetch "$maven/commons-logging/commons-logging/$COMMONS_LOGGING_VERSION/commons-logging-$COMMONS_LOGGING_VERSION.jar" "$COMMONS_LOGGING_SHA256" "$dest/lib/commons-logging-$COMMONS_LOGGING_VERSION.jar"

kotlinc=${KOTLINC:-$(command -v kotlinc || true)}
if [ -z "$kotlinc" ]; then
  cache="$dest/kotlin"
  mkdir -p "$cache"
  fetch "https://github.com/JetBrains/kotlin/releases/download/v$KOTLIN_VERSION/kotlin-compiler-$KOTLIN_VERSION.zip" "$KOTLIN_SHA256" "$cache/kotlin.zip"
  [ -x "$cache/kotlinc/bin/kotlinc" ] || (cd "$cache" && unzip -q -o kotlin.zip)
  kotlinc="$cache/kotlinc/bin/kotlinc"
fi
jars=("$dest"/lib/*.jar)
classpath="$(IFS=:; echo "${jars[*]}")"
"$kotlinc" "$root/jvm/PdfaStamper.kt" -cp "$classpath" -include-runtime -nowarn -d "$dest/pdfa-stamper.jar"
echo "built $dest/pdfa-stamper.jar"
