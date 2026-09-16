"""Live probe #2: prose chunking methods, validation errors, retrieval metadata."""
import io
import json
import time

import requests

BASE = "http://localhost:5000"
s = requests.Session()
assert s.post(f"{BASE}/login", json={"username": "admin", "password": "admin"}).json()["success"]


def wait(doc_id):
    for _ in range(40):
        st = s.get(f"{BASE}/api/knowledge/status/{doc_id}").json()
        if st.get("status") in ("completed", "error"):
            return st
        time.sleep(1)
    return st


def upload(name, content, ctype, method, size, overlap):
    files = {"document": (name, io.BytesIO(content), ctype)}
    data = {"chunking_method": method, "chunk_size": size, "chunk_overlap": overlap}
    r = s.post(f"{BASE}/api/knowledge/upload", files=files, data=data)
    assert r.status_code == 200 and r.json()["success"], r.text
    doc_id = r.json()["documentId"]
    st = wait(doc_id)
    info = s.get(f"{BASE}/api/knowledge/info/{doc_id}").json()["document"]
    return doc_id, st, info


prose = "\n\n".join(f"Paragraph {i} explains procedure {i} in detail. " * 4 for i in range(25))
did_md, st, info = upload("manual.md", prose.encode(), "text/markdown", "markdown", "400", "50")
print("markdown upload:", st["status"], "chunks=", info["chunk_count"], "method=", info["chunking_method"])
assert st["status"] == "completed" and info["chunk_count"] > 1

did_fx, st, info = upload("notes.txt", ("word " * 800).encode(), "text/plain", "fixed", "300", "0")
print("fixed upload:", st["status"], "chunks=", info["chunk_count"])
assert info["chunk_count"] > 1

# text paste endpoint with properties
r = s.post(f"{BASE}/api/knowledge/text", json={
    "name": "Pasted policy", "content_type": "notes",
    "content": "Sentence one is here. Sentence two follows. " * 30,
    "tags": ["policy"], "allowed_roles": [],
    "chunking_method": "sentence", "chunk_size": "250", "chunk_overlap": "20",
})
print("text paste:", r.status_code, r.json().get("chunking_method"), r.json().get("chunk_size"))
assert r.status_code == 200 and r.json()["chunking_method"] == "sentence"
did_text = r.json()["documentId"]
st = wait(did_text)
info_text = s.get(f"{BASE}/api/knowledge/info/{did_text}").json()["document"]
print("text paste chunks:", info_text["chunk_count"], "status", st["status"])
assert info_text["chunk_count"] > 1

# bad options -> 400
r = s.post(f"{BASE}/api/knowledge/upload",
           files={"document": ("x.txt", io.BytesIO(b"hello"), "text/plain")},
           data={"chunking_method": "bogus"})
print("bad method:", r.status_code, r.json())
assert r.status_code == 400 and "Unknown chunking method" in r.json()["error"]

r = s.post(f"{BASE}/api/knowledge/upload",
           files={"document": ("x.txt", io.BytesIO(b"hello"), "text/plain")},
           data={"chunk_size": "5"})
print("bad size:", r.status_code, r.json())
assert r.status_code == 400

r = s.post(f"{BASE}/api/knowledge/text", json={
    "name": "bad", "content_type": "notes", "content": "hi", "chunking_method": "nope"})
print("bad text method:", r.status_code)
assert r.status_code == 400

# retrieval context attaches structured metadata (no LLM needed)
from src.utils.knowledge_manager import KnowledgeManager
manager = KnowledgeManager()
rows = manager.conn.execute(
    "SELECT chunk_id FROM knowledge_chunk_metadata LIMIT 1"
).fetchall()
chunk_id = rows[0][0]
ctx = manager._get_context_chunks([chunk_id])
print("retrieval ctx metadata:", ctx[0]["metadata"])
assert ctx[0]["metadata"].get("department") and ctx[0]["metadata"].get("year")

print("PROBE2 OK")
