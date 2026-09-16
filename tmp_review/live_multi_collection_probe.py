"""In-process E2E: real embeddings + real Chroma, index into a custom collection,
then confirm cross-collection retrieval returns it (no HTTP embedding flakiness)."""
import os
import sqlite3
import sys
import tempfile
import time

sys.path.insert(0, "/home/vijay/gitrepo/copilot/text2sql")
import requests

from src.utils.knowledge_manager import KnowledgeManager
from src.utils import collection_access, knowledge_ingest

COLLECTION = "probe_multi_coll"
manager = KnowledgeManager()

# warm the model so we can assert it is real (not the random fallback)
emb = manager._get_embedding("warmup")
assert isinstance(emb, list) and len(emb) == 384, type(emb)

# register + create the collection through the same helpers the routes use
collection_access.register_collection(COLLECTION, owner_id=1)
manager.vector_store.init_collection(COLLECTION)

path = tempfile.mktemp(suffix=".txt")
with open(path, "w") as fh:
    fh.write("The Zephyr protocol requires quarterly audits of all turbine telemetry. " * 40)

options = knowledge_ingest.normalize_options({
    "chunking_method": "sentence", "chunk_size": 300, "chunk_overlap": 20,
    "collection_name": COLLECTION,
})
doc_id = manager.process_document(path, "zephyr_notes.txt", options=options)
for _ in range(40):
    status = manager.get_document_status(doc_id)
    if status.get("status") in ("completed", "error"):
        break
    time.sleep(0.5)
print("status:", status)
assert status["status"] == "completed", status

info = manager.get_document_info(doc_id)
print("collection:", info["collection_name"], "chunks:", info["chunk_count"])
assert info["collection_name"] == COLLECTION and info["chunk_count"] > 0
print("chroma count:", requests.get(f"http://localhost:8001/collections/{COLLECTION}").json()["collection"]["count"])

query_emb = manager._get_embedding("What does the Zephyr protocol require?")
hits = manager._search_across_collections(query_emb, None)
found = [h for h in hits if h.get("document_id") == doc_id]
print("merged hits:", len(hits), "from target doc:", len(found),
      "best sim:", round(max((h.get('similarity') or 0) for h in hits), 3) if hits else None)
if found:
    print("best target sim:", round(max((h.get('similarity') or 0) for h in found), 3))
assert found, "custom-collection document was not retrieved"

# deleting the document removes its vectors from the custom collection
manager.delete_document(doc_id)
remaining = requests.get(f"http://localhost:8001/collections/{COLLECTION}").json()["collection"]["count"]
print("count after delete:", remaining)
assert remaining == 0

requests.delete(f"http://localhost:8001/collections/{COLLECTION}")
c = sqlite3.connect("text2sql.db")
c.execute("DELETE FROM resource_role_access WHERE resource_type='vector_collection' AND resource_id=(SELECT access_id FROM vector_collection_registry WHERE name=?)", (COLLECTION,))
c.execute("DELETE FROM vector_collection_registry WHERE name=?", (COLLECTION,))
c.commit(); c.close()
print("docs:", manager.conn.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0])
print("PROBE5 OK")
