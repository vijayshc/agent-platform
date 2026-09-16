#!/bin/bash
# dev_restart_app.sh — serialized restart of the live Text2SQL Flask app.
#
# WHY: many agents work this repository concurrently. Restarting while another
# agent is mid-request, or while another restart is running, contends on the
# SQLite WAL and can leave a request half-served. An exclusive lock on
# /tmp/text2sql-restart.lock makes restarts strictly one-at-a-time.
#
# HOW: flock(1) serializes callers; the app is launched detached
# (setsid + nohup) so it survives this script exiting. start.sh itself SIGTERMs
# the previous instance and execs the new one.
#
# RUN THIS FROM THE HOST SHELL. DSH bash calls run inside a private PID
# namespace and cannot signal the host-managed app; if the old instance cannot
# be replaced the script fails loudly (it never reports a false READY).
#
# Usage: scripts/dev_restart_app.sh
#   PORT may be overridden (e.g. PORT=5099) to exercise a review copy; the live
#   app always runs on 5000 by default.
set -uo pipefail

REPO="/home/vijay/gitrepo/copilot/text2sql"
LOCK="/tmp/text2sql-restart.lock"
APP_LOG="/tmp/flask_app.log"
WRAPPER_LOG="/tmp/text2sql-restart.log"
PORT="${PORT:-5000}"
READY_URL="http://127.0.0.1:${PORT}/login"
export PORT

exec 9>"$LOCK"
if ! flock -w 300 9; then
  echo "dev_restart_app: FAILED — could not acquire $LOCK within 300s" >&2
  exit 1
fi

cd "$REPO" || exit 1

# Start each restart from an empty app log so a stale bind error from a previous
# run can never be mistaken for this one.
: > "$APP_LOG" 2>/dev/null || true

setsid nohup ./start.sh >"$WRAPPER_LOG" 2>&1 </dev/null &

# Phase A: wait until the *previous* instance is actually being replaced.  A
# real restart makes the old process exit (so curl briefly fails) or makes
# start.sh's new process announce itself.  If neither happens and the new
# process reports a bind conflict, the old app was never signalled — fail
# instead of reporting a false READY.
for _ in $(seq 1 30); do
  if grep -q "Address already in use" "$APP_LOG" 2>/dev/null; then
    echo "dev_restart_app: FAILED — port $PORT is still owned by the old app;" >&2
    echo "  start.sh could not replace it. Run this from the host shell (DSH bash" >&2
    echo "  runs in a private PID namespace and cannot signal the host process)." >&2
    exit 1
  fi
  if ! curl -sf -o /dev/null --max-time 2 "$READY_URL"; then
    break  # the old instance is down: the restart is real
  fi
  if grep -q "Running on" "$APP_LOG" 2>/dev/null; then
    break  # the new instance is already up
  fi
  sleep 0.5
done

# Phase B: wait for the *new* app to answer (up to ~60s from launch).  A 200
# alone is not proof: the old instance may still be answering.  READY requires
# evidence that this run's process started, and no bind conflict.
for _ in $(seq 1 60); do
  if curl -sf -o /dev/null --max-time 3 "$READY_URL"; then
    if grep -q "Address already in use" "$APP_LOG" 2>/dev/null; then
      echo "dev_restart_app: FAILED — port $PORT is still owned by the old app." >&2
      exit 1
    fi
    if grep -q "Running on" "$APP_LOG" 2>/dev/null; then
      echo "READY: app serving at $READY_URL"
      exit 0
    fi
  fi
  sleep 1
done

echo "dev_restart_app: FAILED — $READY_URL did not return 200 within 60s" >&2
tail -n 20 "$APP_LOG" >&2 2>/dev/null || true
exit 1
