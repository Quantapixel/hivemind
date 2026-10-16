#!/usr/bin/env bash
# Deploy the Hivemind dashboard to Vercel.
#   FALKORDB_URL=rediss://... HIVEMIND_DASHBOARD_KEY=... [HIVEMIND_TEAMS=team1] scripts/deploy-dashboard.sh
set -euo pipefail
cd "$(dirname "$0")/.."
: "${FALKORDB_URL:?set FALKORDB_URL to the team database URL}"
: "${HIVEMIND_DASHBOARD_KEY:?set HIVEMIND_DASHBOARD_KEY to the access code teammates will type}"
export HIVEMIND_DASHBOARD_KEY HIVEMIND_TEAMS="${HIVEMIND_TEAMS:-}"
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
OUT=$(vercel deploy --prod --yes)
echo "$OUT"
DEPLOY_URL=$(printf '%s' "$OUT" | grep -o 'https://[a-z0-9-]*\.vercel\.app' | head -1)
SITE=$(vercel inspect "$DEPLOY_URL" 2>&1 | grep -o 'https://[a-z0-9.-]*\.vercel\.app' | grep -v "$DEPLOY_URL" | head -1)
SITE=${SITE:-$DEPLOY_URL}
# Remember the dashboard for `hivemind invite` (private file, like the database URL).
python3 - "$SITE" <<'PY'
import json, os, sys
d = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "hivemind")
os.makedirs(d, exist_ok=True)
p = os.path.join(d, "dashboard.json")
fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump({"url": sys.argv[1], "access_code": os.environ["HIVEMIND_DASHBOARD_KEY"], "team": os.environ.get("HIVEMIND_TEAMS", "").split(",")[0]}, f, indent=2)
print(f"\nDashboard: {sys.argv[1]}\nSaved for `hivemind invite` in {p}")
PY
