"""Live probe: upload a CSV with chunking + column roles and verify the index."""
import io
import json
import sys
import time

import requests

BASE = "http://localhost:5000"
s = requests.Session()

r = s.post(f"{BASE}/login", json={"username": "admin", "password": "admin"})
print("login", r.status_code, r.json().get("success"))
assert r.status_code == 200 and r.json().get("success"), r.text

# --- ingest options -------------------------------------------------------
r = s.get(f"{BASE}/api/knowledge/ingest-options")
opts = r.json()
print("ingest-options", r.status_code, [m["id"] for m in opts["methods"]], opts["defaults"])
assert opts["success"] and opts["defaults"]["chunk_size"] == 1000

# --- inspect --------------------------------------------------------------
csv_text = (
    "employee_id,name,description,department,year\n"
    "001,Ada Lovelace,Invented the first algorithm,Engineering,2024\n"
    "002,Grace Hopper,Wrote the first compiler,Engineering,2023\n"
    "003,Alan Turing,Formalised computation,Research,2024\n"
)
files = {"document": ("staff.csv", io.BytesIO(csv_text.encode()), "text/csv")}
r = s.post(f"{BASE}/api/knowledge/inspect", files=files)
insp = r.json()
print("inspect", r.status_code, insp.get("kind"), insp.get("columns"), "rows=", insp.get("row_count"))
assert insp["success"] and insp["kind"] == "tabular"
assert insp["columns"] == ["employee_id", "name", "description", "department", "year"]

# --- upload with properties ----------------------------------------------
files = {"document": ("staff.csv", io.BytesIO(csv_text.encode()), "text/csv")}
data = {
    "tags": "hr,staff",
    "chunking_method": "sentence",
    "chunk_size": "600",
    "chunk_overlap": "40",
    "data_columns": json.dumps(["name", "description"]),
    "metadata_columns": json.dumps(["department", "year"]),
}
r = s.post(f"{BASE}/api/knowledge/upload", files=files, data=data)
up = r.json()
print("upload", r.status_code, up)
assert up["success"], r.text
doc_id = up["documentId"]
assert up["chunking_method"] == "sentence" and up["data_columns"] == ["name", "description"]

# --- wait for completion --------------------------------------------------
status = None
for _ in range(40):
    status = s.get(f"{BASE}/api/knowledge/status/{doc_id}").json()
    if status.get("status") in ("completed", "error"):
        break
    time.sleep(1)
print("status", status)
assert status["status"] == "completed", status

# --- document info --------------------------------------------------------
info = s.get(f"{BASE}/api/knowledge/info/{doc_id}").json()["document"]
print("info chunking:", info["chunking_method"], info["chunk_size"], info["chunk_overlap"])
print("info columns:", info["data_columns"], info["metadata_columns"], "chunks=", info["chunk_count"])
assert info["chunk_count"] == 3
assert info["metadata_columns"] == ["department", "year"]

# --- list documents carries the config ------------------------------------
docs = s.get(f"{BASE}/api/knowledge/documents").json()["documents"]
mine = [d for d in docs if d["id"] == doc_id][0]
assert mine["chunking_method"] == "sentence" and mine["data_columns"] == ["name", "description"]
print("list row chunking:", mine["chunking_method"], mine["chunk_size"], mine["data_columns"], mine["metadata_columns"])

# --- vector store holds raw metadata, DB holds chunk metadata -------------
import subprocess
sql = (
    "SELECT key, value FROM knowledge_chunk_metadata WHERE document_id='%s' ORDER BY key" % doc_id
)
out = subprocess.run(
    ["sqlite3", "text2sql.db", sql], capture_output=True, text=True, cwd="/home/vijay/gitrepo/copilot/text2sql"
)
print("chunk metadata rows:\n" + out.stdout)

r = requests.post("http://localhost:8001/collections/knowledge_chunks/search",
                  json={"query_embeddings": [[0.0] * 384], "n_results": 5,
                        "where": {"document_id": doc_id}})
print("chroma search status", r.status_code)
print(json.dumps(r.json(), indent=2)[:1200])

print("\nDOC_ID", doc_id)
print("PROBE OK")
