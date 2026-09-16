#!/bin/bash
# Stop any existing app gracefully so SQLite can dispose pooled connections
# and checkpoint the WAL before the process exits. SIGKILL mid-write is a
# classic way to leave a database in a bad state.
if pgrep -f "python.*app\.py" >/dev/null 2>&1; then
  pkill -TERM -f "python.*app\.py" 2>/dev/null
  for _ in $(seq 1 20); do
    pgrep -f "python.*app\.py" >/dev/null 2>&1 || break
    sleep 0.5
  done
  pkill -KILL -f "python.*app\.py" 2>/dev/null
  sleep 1
fi

cd /home/vijay/gitrepo/copilot/text2sql

# HTTP MCP servers are endpoints, not subprocesses: the app connects to a URL
# and nothing starts it. Restart the ones this deployment hosts (a row opts in
# with config.service) so the MCP Servers page is reachable on first load.
# --autostart is idempotent and never touches external HTTP servers; logs land
# in logs/mcp-http-<name>.log.
if pgrep -f "scripts/mcp_http_service\.py" >/dev/null 2>&1; then
  pkill -TERM -f "scripts/mcp_http_service\.py" 2>/dev/null
  for _ in $(seq 1 10); do
    pgrep -f "scripts/mcp_http_service\.py" >/dev/null 2>&1 || break
    sleep 0.5
  done
  pkill -KILL -f "scripts/mcp_http_service\.py" 2>/dev/null
fi

if [ -f scripts/mcp_http_service.py ]; then
  ~/anaconda3/bin/python3 scripts/mcp_http_service.py --autostart \
    || echo "WARN: HTTP MCP autostart reported a problem (see logs/mcp-http-*.log)"
fi

exec ~/anaconda3/bin/python3 app.py > /tmp/flask_app.log 2>&1
