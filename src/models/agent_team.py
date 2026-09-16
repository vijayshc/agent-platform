from datetime import datetime
import json
from typing import Optional, Dict, Any, List

from src.utils.database import get_db_connection


class AgentTeam:
    """Represents an Agent Framework agent team persisted in the database.

    Columns:
    - id INTEGER PRIMARY KEY AUTOINCREMENT
    - name TEXT UNIQUE NOT NULL
    - description TEXT
    - config TEXT (JSON) with structure:
        {
          "agents": [
            {
              "name": str,
              "role": str,  # planner/executor/reviewer or freeform
              "system_prompt": str,
              "description": str,  # Agent description for MagenticBuilder
              "tools": [
                 {"server_id": int, "tool_name": str}
              ]
            }
          ],
          "settings": { 
            "max_rounds": int,
            "execution_mode": str,  # "sequential" or "selector"
            "selector_prompt": str,  # Custom selector prompt for MagenticBuilder
            "max_stalls": int  # Max stalls for MagenticBuilder
          }
        }
    - created_at TIMESTAMP
    - updated_at TIMESTAMP
    """

    def __init__(self, id: Optional[int] = None, name: Optional[str] = None, description: Optional[str] = None,
                 config: Optional[Dict[str, Any]] = None, created_at: Optional[datetime] = None,
                 updated_at: Optional[datetime] = None):
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        if isinstance(updated_at, str):
            updated_at = datetime.fromisoformat(updated_at)

        self.id = id
        self.name = name
        self.description = description
        self.config = config if isinstance(config, dict) else json.loads(config) if config else {}
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at or datetime.now()

    @classmethod
    def create_table(cls):
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            '''
            CREATE TABLE IF NOT EXISTS agent_teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT,
                config TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            '''
        )
        conn.commit()
        conn.close()

    @classmethod
    def get_all(cls) -> List["AgentTeam"]:
        conn = get_db_connection()
        conn.row_factory = cls._dict_factory
        cur = conn.cursor()
        cur.execute('SELECT * FROM agent_teams ORDER BY name')
        rows = cur.fetchall()
        conn.close()
        return [cls(**row) for row in rows]

    @classmethod
    def get_by_id(cls, team_id: int) -> Optional["AgentTeam"]:
        conn = get_db_connection()
        conn.row_factory = cls._dict_factory
        cur = conn.cursor()
        cur.execute('SELECT * FROM agent_teams WHERE id = ?', (team_id,))
        row = cur.fetchone()
        conn.close()
        return cls(**row) if row else None

    @classmethod
    def get_by_name(cls, name: str) -> Optional["AgentTeam"]:
        conn = get_db_connection()
        conn.row_factory = cls._dict_factory
        cur = conn.cursor()
        cur.execute('SELECT * FROM agent_teams WHERE name = ?', (name,))
        row = cur.fetchone()
        conn.close()
        return cls(**row) if row else None

    def save(self) -> "AgentTeam":
        """Save the agent team to database with retry logic for lock errors."""
        max_retries = 3
        base_delay = 0.1
        
        for attempt in range(max_retries):
            try:
                conn = get_db_connection()
                try:
                    # Begin immediate transaction to acquire lock quickly
                    conn.execute("BEGIN IMMEDIATE")
                    
                    cur = conn.cursor()
                    if self.id:
                        self.updated_at = datetime.now()
                        cur.execute(
                            '''
                            UPDATE agent_teams
                            SET name = ?, description = ?, config = ?, updated_at = ?
                            WHERE id = ?
                            ''',
                            (self.name, self.description, json.dumps(self.config), self.updated_at, self.id)
                        )
                    else:
                        cur.execute(
                            '''
                            INSERT INTO agent_teams (name, description, config)
                            VALUES (?, ?, ?)
                            ''',
                            (self.name, self.description, json.dumps(self.config))
                        )
                        self.id = cur.lastrowid
                    
                    conn.commit()
                    return self
                    
                except Exception as e:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
                    
            except Exception as e:
                if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                    # Wait with exponential backoff before retrying
                    import time
                    delay = base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
                else:
                    # Re-raise if not a lock error or max retries exceeded
                    raise

    def delete(self) -> bool:
        """Delete the agent team from database with retry logic for lock errors."""
        if not self.id:
            return False
            
        max_retries = 3
        base_delay = 0.1
        
        for attempt in range(max_retries):
            try:
                conn = get_db_connection()
                try:
                    # Begin immediate transaction to acquire lock quickly
                    conn.execute("BEGIN IMMEDIATE")
                    
                    cur = conn.cursor()
                    cur.execute('DELETE FROM agent_teams WHERE id = ?', (self.id,))
                    
                    conn.commit()
                    return True
                    
                except Exception as e:
                    conn.rollback()
                    raise
                finally:
                    conn.close()
                    
            except Exception as e:
                if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                    # Wait with exponential backoff before retrying
                    import time
                    delay = base_delay * (2 ** attempt)
                    time.sleep(delay)
                    continue
                else:
                    # Re-raise if not a lock error or max retries exceeded
                    raise
                    
        return False

    @staticmethod
    def _dict_factory(cursor, row):
        d = {}
        for idx, col in enumerate(cursor.description):
            d[col[0]] = row[idx]
        return d
