#!/usr/bin/env bash
# Create (or update) the DigitalOcean App Platform app from .do/app.yaml.
# Prerequisites:
#   doctl auth init
#   gh auth login
#   git remote origin pointing at the GitHub repo
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v doctl >/dev/null; then
  echo "doctl not found. Install: https://docs.digitalocean.com/reference/doctl/" >&2
  exit 1
fi

if ! doctl account get >/dev/null 2>&1; then
  echo "Not authenticated. Run: doctl auth init" >&2
  exit 1
fi

REMOTE="$(git remote get-url origin 2>/dev/null || true)"
if [[ -z "$REMOTE" ]]; then
  echo "No git remote 'origin'. Create the GitHub repo first:" >&2
  echo "  gh repo create leaderboard --public --source=. --remote=origin --push" >&2
  exit 1
fi

# Derive OWNER/REPO from origin URL
REPO="$(echo "$REMOTE" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')"
echo "Using GitHub repo: $REPO"

SPEC=".do/app.yaml"
TMP="$(mktemp)"
sed "s#REPLACE_OWNER/leaderboard#${REPO}#g" "$SPEC" > "$TMP"

APP_ID="$(doctl apps list -o json 2>/dev/null | python3 -c '
import json,sys
apps=json.load(sys.stdin)
for a in apps:
    if a.get("spec",{}).get("name")=="leaderboard":
        print(a["id"]); break
' || true)"

if [[ -n "${APP_ID}" ]]; then
  echo "Updating existing app $APP_ID ..."
  doctl apps update "$APP_ID" --spec "$TMP"
else
  echo "Creating DigitalOcean app ..."
  doctl apps create --spec "$TMP"
fi

rm -f "$TMP"
echo
echo "List apps:  doctl apps list"
echo "Live URL:   doctl apps list --format DefaultIngress,Spec.Name --no-header"
echo
echo "Also add GitHub secret DIGITALOCEAN_ACCESS_TOKEN so .github/workflows/deploy.yml can redeploy on push."
