/** Copy-paste request samples for the agent's public HTTP API.
 *
 * Every snippet drives the same flow: exchange a platform username/password
 * for a Bearer JWT at ``POST /login``, then call the agent with that token so
 * the platform's role-based agent access is enforced per request. The samples
 * live apart from the dialog component so the UI stays a thin shell.
 */

export type SampleTab = "curl" | "python" | "javascript";

export const SAMPLE_TABS = [
  { id: "curl", label: "cURL", language: "shell", filename: "run.sh" },
  { id: "python", label: "Python", language: "python", filename: "run.py" },
  { id: "javascript", label: "JavaScript", language: "javascript", filename: "run.mjs" },
] as const;

export interface SampleUrls {
  /** Origin the platform is served from, e.g. ``http://localhost:5000``. */
  baseUrl: string;
  /** ``POST /login`` — username/password → Bearer JWT. */
  loginUrl: string;
  /** ``POST /api/v1/runs`` — run the agent, SSE when ``stream`` is true. */
  runsUrl: string;
  /** ``POST /api/v1/agents/<slug>/invoke`` — one JSON reply (published agents). */
  invokeUrl: string;
}

const SAMPLE_INPUT = "Which customer purchased the most?";

/** Real request samples for the platform's public run and HITL endpoints. */
export function buildSamples(urls: SampleUrls, slug: string): Record<SampleTab, string> {
  const { baseUrl, loginUrl, runsUrl, invokeUrl } = urls;

  const curl = `# 1. Exchange your platform credentials for a Bearer token (JWT, valid 12 h).
TOKEN=$(curl -s -X POST "${loginUrl}" \\
  -H "Content-Type: application/json" \\
  -d '{"username":"'"$TEXT2SQL_USERNAME"'","password":"'"$TEXT2SQL_PASSWORD"'"}' \\
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

AUTH="Authorization: Bearer $TOKEN"

# 2. Streamed run: stream=true returns Server-Sent Events. run_id below is the
#    "public_id" reported on the stream.
curl -N -X POST "${runsUrl}" \\
  -H "$AUTH" -H "Content-Type: application/json" \\
  -d '{"agent_id": "${slug}", "input": "${SAMPLE_INPUT}", "stream": true}'
#   data: {"type":"status","status":"starting", ...}
#   data: {"type":"token","content":"The "}
#   data: {"type":"reasoning","content":"..."}
#   data: {"type":"approval_request","action_requests":[{"name":"execute_sql_query","args":{...}}],"review_configs":[...]}
#   data: {"type":"done","reply":"...","status":"awaiting_approval"}

# 3. The agent paused for approval: send one decision per action request.
curl -N -X POST "${runsUrl}/<run_id>/resume" \\
  -H "$AUTH" -H "Content-Type: application/json" \\
  -d '{"decisions": [{"type": "approve"}], "stream": true}'
#   Other decisions:
#     {"type":"reject","message":"not on production"}
#     {"type":"edit","edited_action":{"name":"execute_sql_query","args":{...}}}
#     {"type":"respond","message":"the human's answer"}

# 4. Non-streamed run: one JSON response with the final reply (published agents).
curl -s -X POST "${invokeUrl}" \\
  -H "$AUTH" -H "Content-Type: application/json" \\
  -d '{"input": "${SAMPLE_INPUT}"}'
# -> {"run": {...}, "events": [...], "reply": "...", "status": "success"}`;

  const python = `"""Run a platform agent over HTTP with a Bearer (JWT) access token.

The token identifies you, so the agent's role-based access rules are enforced
on every request. Get one by posting your platform username and password to
/login; it is valid for 12 hours.
"""
import json
import os

import requests

BASE_URL = "${baseUrl}"
AGENT_ID = "${slug}"

# 1. Exchange credentials for a Bearer token.
login = requests.post(
    f"{BASE_URL}/login",
    json={
        "username": os.environ["TEXT2SQL_USERNAME"],
        "password": os.environ["TEXT2SQL_PASSWORD"],
    },
    timeout=30,
)
login.raise_for_status()
headers = {"Authorization": f"Bearer {login.json()['access_token']}"}


def stream(url, payload):
    """POST and print SSE text; return (reply, pending approval, run id)."""
    reply, approval, run_id, streamed = "", None, None, False
    with requests.post(url, headers=headers, json=payload, stream=True, timeout=600) as res:
        res.raise_for_status()
        for line in res.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data: "):
                continue  # ": keepalive" comments keep the connection alive
            event = json.loads(line[len("data: "):])
            kind = event["type"]
            run_id = event.get("public_id") or run_id
            if kind == "token":
                streamed = True
                print(event.get("content") or "", end="", flush=True)
            elif kind == "approval_request":
                approval = event
            elif kind == "done":
                reply = event.get("reply") or reply
            elif kind == "error":
                raise RuntimeError(event["message"])
    if not streamed and reply:
        print(reply, end="", flush=True)  # a model that skipped token deltas
    return reply, approval, run_id


# 2. Streamed run.
reply, approval, run_id = stream(
    f"{BASE_URL}/api/v1/runs",
    {"agent_id": AGENT_ID, "input": "${SAMPLE_INPUT}", "stream": True},
)

# 3. The agent paused before a tool call: one decision per action request.
while approval is not None:
    decisions = []
    for action in approval["action_requests"]:
        decisions.append({"type": "approve"})
        # Other decisions:
        #   {"type": "reject",  "message": "not on production"}
        #   {"type": "edit",    "edited_action": {"name": action["name"], "args": {...}}}
        #   {"type": "respond", "message": "the human's answer"}
    reply, approval, run_id = stream(
        f"{BASE_URL}/api/v1/runs/{run_id}/resume",
        {"decisions": decisions, "stream": True},
    )

print()  # end the streamed answer's line

# 4. Non-streamed run: one JSON response with the final reply (published
#    agents). A pause comes back as status "awaiting_approval"; resume it with
#    the same POST /runs/<id>/resume body shown above.
result = requests.post(
    f"{BASE_URL}/api/v1/agents/{AGENT_ID}/invoke",
    headers=headers,
    json={"input": "${SAMPLE_INPUT}"},
    timeout=600,
).json()
print(result["status"], result["reply"])`;

  const javascript = `// Node 18+ (global fetch, TextDecoder, process.env).
const BASE_URL = "${baseUrl}";
const AGENT_ID = "${slug}";

// 1. Exchange credentials for a Bearer token.
const login = await fetch(\`\${BASE_URL}/login\`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({
    username: process.env.TEXT2SQL_USERNAME,
    password: process.env.TEXT2SQL_PASSWORD,
  }),
});
if (!login.ok) throw new Error(await login.text());
const { access_token } = await login.json();
const headers = {
  "Content-Type": "application/json",
  Authorization: \`Bearer \${access_token}\`,
};

// 2. POST and stream SSE events; return the reply and any pending approval.
async function stream(url, payload) {
  const res = await fetch(url, { method: "POST", headers, body: JSON.stringify(payload) });
  if (!res.ok) throw new Error(await res.text());
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "", reply = "", approval = null, runId = null, streamed = false;
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\\n\\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      if (!frame.startsWith("data: ")) continue; // ": keepalive" comments
      const event = JSON.parse(frame.slice(6));
      runId = event.public_id ?? runId;
      if (event.type === "token") {
        streamed = true;
        process.stdout.write(event.content ?? "");
      } else if (event.type === "approval_request") approval = event;
      else if (event.type === "done") reply = event.reply ?? reply;
      else if (event.type === "error") throw new Error(event.message);
    }
  }
  if (!streamed && reply) process.stdout.write(reply); // no token deltas sent
  return { reply, approval, runId };
}

// 3. Streamed run.
let { reply, approval, runId } = await stream(
  \`\${BASE_URL}/api/v1/runs\`,
  { agent_id: AGENT_ID, input: "${SAMPLE_INPUT}", stream: true },
);

// 4. The agent paused before a tool call: one decision per action request.
while (approval) {
  const decisions = approval.action_requests.map(() => ({ type: "approve" }));
  // Other decisions:
  //   { type: "reject", message: "not on production" }
  //   { type: "edit", edited_action: { name, args } }
  //   { type: "respond", message: "the human's answer" }
  ({ reply, approval, runId } = await stream(
    \`\${BASE_URL}/api/v1/runs/\${runId}/resume\`,
    { decisions, stream: true },
  ));
}

console.log(); // end the streamed answer's line

// 5. Non-streamed run: one JSON response (published agents).
const sync = await fetch(\`\${BASE_URL}/api/v1/agents/\${AGENT_ID}/invoke\`, {
  method: "POST",
  headers,
  body: JSON.stringify({ input: "${SAMPLE_INPUT}" }),
});
const result = await sync.json();
console.log(result.status, result.reply);`;

  return { curl, python, javascript };
}
