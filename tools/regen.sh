#!/bin/sh
# Regenerate the translated test suites and example projects from their async/await originals.
set -e
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
for style in generators asyncgen greenlets; do
  mkdir -p tests/conformance/$style
  for f in tests/conformance/asyncawait/*.py; do
    base=$(basename "$f")
    case "$base" in conftest.py|__init__.py) continue;; esac
    $PY tools/translate.py $style "$f" tests/conformance/$style/$base
  done
  for ex in examples/*/asyncawait; do
    [ -d "$ex" ] || continue
    dst=$(dirname "$ex")/$style
    rm -rf "$dst"; mkdir -p "$dst"
    (cd "$ex" && find . -type d) | while read d; do mkdir -p "$dst/$d"; done
    (cd "$ex" && find . -name '*.py') | while read f; do $PY tools/translate.py $style "$ex/$f" "$dst/$f"; done
    (cd "$ex" && find . -type f ! -name '*.py' ! -path '*__pycache__*') | while read f; do cp "$ex/$f" "$dst/$f"; done
  done
done

for style in generators_noco asyncgen_noco greenlets_noco; do
  if [ -d tests/conformance/asyncawait_noco ]; then
    mkdir -p tests/conformance/$style
    for f in tests/conformance/asyncawait_noco/*.py; do
      base=$(basename "$f")
      case "$base" in conftest.py|__init__.py) continue;; esac
      $PY tools/translate.py $style "$f" tests/conformance/$style/$base
    done
  fi
  for ex in examples/*/asyncawait_noco; do
    [ -d "$ex" ] || continue
    dst=$(dirname "$ex")/$style
    rm -rf "$dst"; mkdir -p "$dst"
    (cd "$ex" && find . -type d) | while read d; do mkdir -p "$dst/$d"; done
    (cd "$ex" && find . -name '*.py') | while read f; do $PY tools/translate.py $style "$ex/$f" "$dst/$f"; done
    (cd "$ex" && find . -type f ! -name '*.py' ! -path '*__pycache__*') | while read f; do cp "$ex/$f" "$dst/$f"; done
  done
done
