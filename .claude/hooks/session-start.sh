#!/bin/bash
# SessionStart hook for Claude Code on the web: install the OLIVER harness
# dependencies so the constraint checker, tests and eval suite can run.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "$CLAUDE_PROJECT_DIR"

# Some base images ship a distro cryptography build without its cffi backend,
# which crashes `import litellm`. Shadow it with a working wheel when needed.
if ! python3 -c "import cryptography.hazmat.primitives.hashes" >/dev/null 2>&1; then
  python3 -m pip install --quiet --disable-pip-version-check --ignore-installed \
    "cryptography>=42" cffi
fi

python3 -m pip install --quiet --disable-pip-version-check \
  -r oliver/requirements.txt

# Report constraint status into the session context (never blocks startup).
python3 scripts/check_constraints.py || true
