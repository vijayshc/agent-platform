"""Remove the probe/test documents created during live verification."""
import sqlite3
import sys

sys.path.insert(0, "/home/vijay/gitrepo/copilot/text2sql")
from src.utils.knowledge_manager import KnowledgeManager

PATH = "/home/vijay/gitrepo/copilot/text2sql/text2sql.db"
conn = sqlite3.connect(PATH)
conn.row_factory = sqlite3.Row
targets = conn.execute(
    "SELECT id, original_filename, created_at FROM knowledge_documents "
    "WHERE created_at >= '2026-09-17T06:40:00' "
    "AND original_filename IN ('staff.csv','manual.md','notes.txt','Pasted policy','people.csv') "
    "ORDER BY created_at"
).fetchall()
conn.close()

print("to delete:")
for t in targets:
    print(" ", t["id"], t["original_filename"], t["created_at"])

manager = KnowledgeManager()
for t in targets:
    ok = manager.delete_document(t["id"])
    print("deleted" if ok else "FAILED", t["original_filename"], t["id"])

conn = sqlite3.connect(PATH)
print("remaining docs:", conn.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0])
print("remaining metadata rows:", conn.execute("SELECT COUNT(*) FROM knowledge_chunk_metadata").fetchone()[0])
conn.close()
