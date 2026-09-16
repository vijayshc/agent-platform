"""Live probe #3: end-to-end knowledge QA against the live LLM."""
import json
import sqlite3
import sys

sys.path.insert(0, "/home/vijay/gitrepo/copilot/text2sql")
from src.utils.knowledge_manager import KnowledgeManager

conn = sqlite3.connect("/home/vijay/gitrepo/copilot/text2sql/text2sql.db")
conn.row_factory = sqlite3.Row
admin = conn.execute("SELECT id, username FROM users WHERE username = 'admin'").fetchone()
print("admin user:", dict(admin))
conn.close()

manager = KnowledgeManager()
for question in [
    "Who works in the Research department?",
    "What did Grace Hopper create?",
]:
    result = manager.get_answer(question, user_id=admin["id"])
    print("\nQ:", question)
    print("success:", result.get("success"))
    print("answer:", (result.get("answer") or "")[:400])
    print("sources:", json.dumps(result.get("sources"), default=str))
