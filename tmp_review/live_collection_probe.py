"""Live probe #4: per-upload collection selection, grants, and multi-collection retrieval."""
import io
import json
import sqlite3
import sys
import time

import requests

sys.path.insert(0, "/home/vijay/gitrepo/copilot/text2sql")
BASE = "http://localhost:5000"
COLLECTION = "hr_records_probe"

s = requests.Session()
assert s.post(f"{BASE}/login", json={"username": "admin", "password": "admin"}).json()["success"], "login failed"

# 1. admin sees collections; vector-db listing registers them (claims for admin)
before = s.get(f"{BASE}/api/knowledge/collections").json()
print("collections before:", [c["name"] for c in before["collections"]])
vd = s.get(f"{BASE}/admin/api/vector-db/collections").json()
print("vector-db collections:", [(c["name"], c.get("access_id"), c.get("restricted")) for c in vd["collections"]])

# 2. create a collection (admin only) and confirm it exists
r = s.post(f"{BASE}/admin/api/vector-db/collections", json={"name": COLLECTION})
print("create:", r.status_code, r.json())
assert r.status_code in (200, 409), r.text

# bad name rejected
r = s.post(f"{BASE}/admin/api/vector-db/collections", json={"name": "x"})
print("create bad name:", r.status_code, r.json().get("error"))
assert r.status_code == 400

r = s.post(f"{BASE}/admin/api/vector-db/collections", json={"name": COLLECTION})
print("create duplicate:", r.status_code)
assert r.status_code == 409

# 3. it appears in the knowledge dropdown with an access_id
after = s.get(f"{BASE}/api/knowledge/collections").json()["collections"]
entry = [c for c in after if c["name"] == COLLECTION][0]
print("knowledge collection entry:", entry)
assert entry["access_id"] is not None

# 4. upload a CSV into that collection
csv_text = (
    "employee_id,name,description,department,year\n"
    "001,Ada Lovelace,Invented the first algorithm,Engineering,2024\n"
    "002,Grace Hopper,Wrote the first compiler,Engineering,2023\n"
)
files = {"document": ("probe_staff.csv", io.BytesIO(csv_text.encode()), "text/csv")}
data = {
    "data_columns": json.dumps(["name", "description"]),
    "metadata_columns": json.dumps(["department", "year"]),
    "collection_name": COLLECTION,
}
r = s.post(f"{BASE}/api/knowledge/upload", files=files, data=data)
up = r.json()
print("upload:", up.get("success"), up.get("collection_name"))
assert up["success"] and up["collection_name"] == COLLECTION
doc_id = up["documentId"]
for _ in range(40):
    st = s.get(f"{BASE}/api/knowledge/status/{doc_id}").json()
    if st.get("status") in ("completed", "error"):
        break
    time.sleep(1)
info = s.get(f"{BASE}/api/knowledge/info/{doc_id}").json()["document"]
print("doc:", st["status"], "collection=", info["collection_name"], "chunks=", info["chunk_count"])
assert st["status"] == "completed" and info["collection_name"] == COLLECTION and info["chunk_count"] == 2

# 5. vectors landed in the chosen collection
r = requests.get(f"http://localhost:8001/collections/{COLLECTION}")
count = r.json().get("collection", {}).get("count")
print("chroma count:", count)
assert count == 2

# 6. multi-collection retrieval finds the row chunk
from src.utils.knowledge_manager import KnowledgeManager
manager = KnowledgeManager()
emb = manager._get_embedding("Who invented the first algorithm?")
hits = manager._search_across_collections(emb, None)
cols = {h.get("document_id") for h in hits}
print("retrieval hits:", len(hits), "includes new doc:", doc_id in cols)
assert doc_id in cols

# 7. grant role 'user' (id 2) on the collection through the SHARED access API
users = sqlite3.connect("text2sql.db"); users.row_factory = sqlite3.Row
role_user = users.execute("SELECT id FROM roles WHERE name='user'").fetchone()["id"]
users.close()
r = s.put(f"{BASE}/api/v1/access/vector_collection/{entry['access_id']}", json={"role_ids": [role_user]})
print("grant:", r.status_code, [(a["role_name"]) for a in r.json()["access"]])
assert r.status_code == 200

from src.utils import collection_access
names = [COLLECTION, "knowledge_chunks"]
print("visible for role-holder user 2:", collection_access.visible_collection_names(names, 2))
print("visible for admin:", collection_access.visible_collection_names(names, 1))
assert collection_access.can_access_collection(COLLECTION, 2)
assert not collection_access.can_access_collection("knowledge_chunks", 2)

# 8. the Vector DB list reflects the grant
vd2 = s.get(f"{BASE}/admin/api/vector-db/collections").json()["collections"]
probe = [c for c in vd2 if c["name"] == COLLECTION][0]
print("vd restricted/roles:", probe["restricted"], probe["roles"])
assert probe["restricted"] and "user" in probe["roles"]

# cleanup: delete the probe doc (its vectors live in the new collection) and leave collection
s.delete(f"{BASE}/api/knowledge/delete/{doc_id}")
# remove the probe collection itself (no app delete route; use the vector DB)
requests.delete(f"http://localhost:8001/collections/{COLLECTION}")
import sqlite3 as _sq
_c = _sq.connect("text2sql.db")
_c.execute("DELETE FROM resource_role_access WHERE resource_type=? AND resource_id=?", ("vector_collection", entry["access_id"]))
_c.execute("DELETE FROM vector_collection_registry WHERE name=?", (COLLECTION,))
_c.commit(); _c.close()
print("PROBE4 OK")
