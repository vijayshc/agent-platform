#!/bin/bash
# dev_build_frontend.sh — serialized production build of the React agent app.
#
# WHY: many agents edit frontend/agent-app concurrently. Two simultaneous
# `npm run build` runs race on the same output directory (static/agent-app) and
# on the Vite cache. An exclusive lock on /tmp/text2sql-frontend-build.lock
# makes builds strictly one-at-a-time, so the last build is always a complete
# artifact.
#
# HOW: flock(1) serializes callers, then `npm run build` writes to
# static/agent-app (vite.config.ts outDir). Prints a clear BUILD OK line.
#
# Usage: scripts/dev_build_frontend.sh
set -uo pipefail

REPO="/home/vijay/gitrepo/copilot/text2sql"
LOCK="/tmp/text2sql-frontend-build.lock"

exec 9>"$LOCK"
if ! flock -w 900 9; then
  echo "dev_build_frontend: FAILED — could not acquire $LOCK within 900s" >&2
  exit 1
fi

cd "$REPO/frontend/agent-app" || exit 1

if npm run build; then
  echo "BUILD OK: static/agent-app updated"
  exit 0
fi

echo "BUILD FAILED: npm run build returned a non-zero status" >&2
exit 1
