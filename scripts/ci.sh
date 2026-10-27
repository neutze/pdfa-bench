#!/usr/bin/env bash
# The gate: ruff, gitleaks over the history, then the image build, whose last step is the
# self-test (every lane, veraPDF included), so a green gate means the image a user builds works.
# Its last line, "pdfa-bench gate: ok", is the merge queue's and the release's GATE_OK.
#
# The build holds the Mac's one image-build lock (shared with the other repositories' builds) unless
# the caller already holds it (DOCKER_BUILD_LOCKED, set by docker-ci's PR gates).
#
# Run: ./scripts/ci.sh
set -euo pipefail
cd "$(dirname "$0")/.."

lock="${XDG_STATE_HOME:-$HOME/.local/state}/docker-build.lock"
if [ -z "${DOCKER_BUILD_LOCKED:-}" ] && command -v lockf >/dev/null; then
    mkdir -p "$(dirname "$lock")"
    export DOCKER_BUILD_LOCKED=1
    exec lockf -k -t 3600 "$lock" "$0" "$@"
fi

echo "== ruff"
uvx ruff check --select F,E9 src slides

echo "== gitleaks"
command -v gitleaks >/dev/null || {
    echo "gitleaks not installed: brew install gitleaks" >&2
    exit 1
}
if git rev-parse --verify -q HEAD >/dev/null; then
    gitleaks git --no-banner --log-level warn .
else
    gitleaks dir --no-banner --log-level warn .
fi

echo "== image build and self-test"
./pdfa-bench --rebuild list

echo "pdfa-bench gate: ok"
