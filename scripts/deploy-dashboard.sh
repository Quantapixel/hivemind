#!/usr/bin/env bash
# Deploy the Hivemind dashboard to Vercel.
#   FALKORDB_URL=rediss://... HIVEMIND_DASHBOARD_KEY=... [HIVEMIND_TEAMS=team1] scripts/deploy-dashboard.sh
set -euo pipefail
cd "$(dirname "$0")/.."
: "${FALKORDB_URL:?set FALKORDB_URL to the team database URL}"
: "${HIVEMIND_DASHBOARD_KEY:?set HIVEMIND_DASHBOARD_KEY to the access code teammates will type}"
DIR=deploy/vercel
rm -rf "$DIR/hivemind"
rsync -a --exclude __pycache__ src/hivemind/ "$DIR/hivemind/"
cd "$DIR"
vercel link --yes --project "${VERCEL_PROJECT:-hivemind-dashboard}" >/dev/null
for name in FALKORDB_URL HIVEMIND_DASHBOARD_KEY HIVEMIND_TEAMS; do
  vercel env rm "$name" production --yes >/dev/null 2>&1 || true
  if [ -n "${!name:-}" ]; then printf '%s' "${!name}" | vercel env add "$name" production >/dev/null; fi
done
# Functions run in the region set in vercel.json ("regions"); keep it next to the
# database (bom1 = Mumbai for an ap-south-1 instance) or every refresh crosses the world.
vercel deploy --prod --yes
