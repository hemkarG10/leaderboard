#!/usr/bin/env bash
# One-shot: commit (if needed), create public GitHub repo, push, print next steps for DO.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v gh >/dev/null; then
  echo "gh CLI not found" >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "GitHub CLI not authenticated. Run: gh auth login" >&2
  exit 1
fi

if [[ ! -d .git ]]; then
  git init
  git branch -M main
fi

if [[ -z "$(git status --porcelain 2>/dev/null || true)" ]] && git rev-parse HEAD >/dev/null 2>&1; then
  echo "Working tree clean."
else
  git add -A
  # Respect user commit preference — only commit here because bootstrap needs a root commit.
  git commit -m "$(cat <<'EOF'
feat: leaderboard API with CI and DigitalOcean deploy

In-memory per-game leaderboards, checklist tests, GitHub Actions CI/CD,
and App Platform Dockerfile spec.
EOF
)" || true
fi

if git remote get-url origin >/dev/null 2>&1; then
  echo "Remote origin already set: $(git remote get-url origin)"
  git push -u origin HEAD
else
  gh repo create leaderboard --public --source=. --remote=origin --push
fi

echo
echo "Next:"
echo "  1. gh secret set DIGITALOCEAN_ACCESS_TOKEN   # paste a DO API token"
echo "  2. doctl auth init"
echo "  3. ./scripts/do-create.sh"
echo "  4. gh run watch   # wait for CI green"
