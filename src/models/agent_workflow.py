from datetime import datetime
import json
from typing import Optional, Dict, Any, List

from src.utils.database import get_db_connection


class AgentWorkflow:
    """Represents a saved workflow definition for an Agent Framework team.

    Columns:
    - id INTEGER PRIMARY KEY AUTOINCREMENT
    - name TEXT UNIQUE NOT NULL
    - description TEXT
    - team_id INTEGER REFERENCES agent_teams(id)
    - graph TEXT (JSON) with nodes/edges and metadata
    - created_at TIMESTAMP
    - updated_at TIMESTAMP
    """

    def __init__(self, id: Optional[int] = None, name: Optional[str] = None, description: Optional[str] = None,
                 team_id: Optional[int] = None, graph: Optional[Dict[str, Any]] = None,
                 created_at: Optional[datetime] = None, updated_at: Optional[datetime] = None):
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)

        self.id = id
        self.name = name
        self.description = description
        self.team_id = team_id
        self.graph = graph if isinstance(graph, dict) else json.loads(graph) if graph else {}
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at or datetime.now()

    @classmethod
    def create_table(cls):
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            '''
            CREATE TABLE IF NOT EXISTS agent_workflows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                team_id INTEGER NOT NULL,
                graph TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(team_id) REFERENCES agent_teams(id)
            )
            '''
        )
        conn.commit()
        conn.close()

    @classmethod
    def get_all(cls) -> List["AgentWorkflow"]:
        conn = get_db_connection()
        conn.row_factory = cls._dict_factory
        cur = conn.cursor()
        cur.execute('SELECT * FROM agent_workflows ORDER BY name')
        rows = cur.fetchall()
        conn.close()
        return [cls(**row) for row in rows]

    @classmethod
    def get_by_id(cls, wf_id: int) -> Optional["AgentWorkflow"]:
        conn = get_db_connection()
        conn.row_factory = cls._dict_factory
        cur = conn.cursor()
        cur.execute('SELECT * FROM agent_workflows WHERE id = ?', (wf_id,))
        row = cur.fetchone()
        conn.close()
        return cls(**row) if row else None

    @classmethod
    def get_by_name(cls, name: str) -> Optional["AgentWorkflow"]:
        conn = get_db_connection()
        conn.row_factory = cls._dict_factory
        cur = conn.cursor()
        cur.execute('SELECT * FROM agent_workflows WHERE name = ?', (name,))
        row = cur.fetchone()
        conn.close()
        return cls(**row) if row else None

    def save(self) -> "AgentWorkflow":
        conn = get_db_connection()
        cur = conn.cursor()
        if self.id:
            self.updated_at = datetime.now()
            cur.execute(
                '''
                UPDATE agent_workflows
                SET name = ?, description = ?, team_id = ?, graph = ?, updated_at = ?
                WHERE id = ?
                ''',
                (self.name, self.description, self.team_id, json.dumps(self.graph), self.updated_at, self.id)
            )
        else:
            # Check if a workflow with this name already exists
            cur.execute('SELECT id FROM agent_workflows WHERE name = ?', (self.name,))
            existing = cur.fetchone()
            if existing:
                # Update the existing workflow instead of creating a new one
                self.id = existing[0]
                self.updated_at = datetime.now()
                cur.execute(
                    '''
                    UPDATE agent_workflows
                    SET description = ?, team_id = ?, graph = ?, updated_at = ?
                    WHERE id = ?
                    ''',
                    (self.description, self.team_id, json.dumps(self.graph), self.updated_at, self.id)
                )
            else:
                cur.execute(
                    '''
                    INSERT INTO agent_workflows (name, description, team_id, graph)
                    VALUES (?, ?, ?, ?)
                    ''',
                    (self.name, self.description, self.team_id, json.dumps(self.graph))
                )
                self.id = cur.lastrowid
        conn.commit()
        conn.close()
        return self

    def delete(self) -> bool:
        if not self.id:
            return False
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute('DELETE FROM agent_workflows WHERE id = ?', (self.id,))
        conn.commit()
        conn.close()
        return True

    @staticmethod
    def _dict_factory(cursor, row):
        d = {}
        for idx, col in enumerate(cursor.description):
            d[col[0]] = row[idx]
        return d
