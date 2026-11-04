#!/usr/bin/env bash
# Fail if anything under tests/ changed since the latest tests-locked-* tag (or the tag given).
set -euo pipefail
TAG="${1:-$(git tag --list 'tests-locked-*' --sort=-creatordate | head -n1)}"
if [[ -z "$TAG" ]]; then
  echo "no tests-locked-* tag found" >&2
  exit 1
fi
if ! git diff --quiet "$TAG" -- tests/ || [[ -n "$(git status --porcelain -- tests/)" ]]; then
  echo "tests/ changed since $TAG:" >&2
  git diff --stat "$TAG" -- tests/ >&2
  git status --porcelain -- tests/ >&2
  exit 1
fi
echo "tests/ unchanged since $TAG"
