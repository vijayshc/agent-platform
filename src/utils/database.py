from sqlalchemy import create_engine, event, text, pool
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker, scoped_session
from src.utils.schema_manager import SchemaManager
import pandas as pd
import logging
import time
import sqlite3
from config.config import DATABASE_URI

# Create a global session factory
_Session = None


def _apply_sqlite_pragmas(dbapi_conn):
    """Apply the same concurrency/safety PRAGMAs to every SQLite connection.

    ``busy_timeout`` is set first so the subsequent ``journal_mode`` switch
    waits for locks instead of failing immediately. ``journal_mode`` is only
    changed when the database is not already in WAL: switching it takes a
    write lock, and doing that on every connection open adds needless lock
    contention under concurrency.
    """
    cursor = dbapi_conn.cursor()
    try:
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA temp_store=MEMORY")
        # Bounded per-connection page cache. A very large cache multiplied by
        # many concurrent connections (runs + requests + pool) can exhaust
        # memory and invite OOM kills mid-write.
        cursor.execute("PRAGMA cache_size=-8000")
        cursor.execute("PRAGMA wal_autocheckpoint=1000")
        row = cursor.execute("PRAGMA journal_mode").fetchone()
        if not (row and str(row[0]).lower() == "wal"):
            cursor.execute("PRAGMA journal_mode=WAL")
    except sqlite3.Error:
        pass
    finally:
        cursor.close()


def create_db_engine(uri: str = DATABASE_URI):
    """Build a SQLite-safe SQLAlchemy engine.

    SQLite connections must never be shared between threads. ``NullPool`` opens
    a fresh connection for each checkout and closes it on return, so a session
    and its connection stay on one thread and a burst of requests cannot
    exhaust a fixed pool. A shared pool (StaticPool) is specifically avoided:
    it hands the same connection to every thread and lets concurrent sessions
    interleave BEGIN/COMMIT on it, which corrupts the database.
    """
    engine_args = {
        'pool_pre_ping': True,
        'pool_recycle': 300,
        'echo': False,
    }
    is_sqlite = uri.startswith('sqlite:')
    if is_sqlite:
        engine_args.update({
            'connect_args': {
                'check_same_thread': False,
                'timeout': 30,
            },
            'poolclass': pool.NullPool,
        })

    engine = create_engine(uri, **engine_args)

    if is_sqlite:
        @event.listens_for(engine, "connect")
        def _on_connect(dbapi_conn, _connection_record):
            _apply_sqlite_pragmas(dbapi_conn)

    return engine


def get_db_session():
    """Get a scoped database session
    
    Returns:
        sqlalchemy.orm.Session: A scoped SQLAlchemy session
    """
    global _Session
    
    if _Session is None:
        logger = logging.getLogger('text2sql.database')
        logger.info(f"Creating new database session factory with URI: {DATABASE_URI}")
        _Session = scoped_session(sessionmaker(bind=create_db_engine(DATABASE_URI)))
    
    return _Session()


def remove_db_session():
    """Release the current thread's session and its pooled connection.

    Call this at the end of a request or worker thread so the connection
    returns to the pool instead of being held for the thread's lifetime.
    """
    global _Session
    if _Session is not None:
        _Session.remove()


def dispose_db_engine() -> None:
    """Close every pooled ORM connection (call on graceful shutdown)."""
    global _Session
    if _Session is None:
        return
    try:
        _Session.remove()
    except Exception:
        pass
    try:
        _Session.bind.dispose()
    except Exception:
        pass
    _Session = None


def checkpoint_wal() -> None:
    """Fold the WAL back into the main database file (best effort)."""
    try:
        conn = get_db_connection()
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
    except sqlite3.Error:
        pass


def quick_check() -> str:
    """Return the database quick_check result (``"ok"`` when healthy)."""
    try:
        conn = get_db_connection()
        try:
            rows = conn.execute("PRAGMA quick_check").fetchall()
        finally:
            conn.close()
        return "; ".join(str(r[0]) for r in rows) if rows else "ok"
    except sqlite3.Error as exc:
        return f"error: {exc}"

def get_db_connection():
    """Get a raw SQLite database connection with proper timeout and retry handling
    
    Returns:
        sqlite3.Connection: A SQLite connection object
    """
    # Extract the SQLite file path from the SQLAlchemy URI
    if DATABASE_URI.startswith('sqlite:///'):
        db_path = DATABASE_URI[10:]  # Remove sqlite:///
    else:
        # Default to text2sql.db if URI parsing fails
        db_path = 'text2sql.db'
    
    # Retry connection with exponential backoff for database lock issues
    max_retries = 5
    base_delay = 0.1
    
    for attempt in range(max_retries):
        try:
            conn = sqlite3.connect(
                db_path,
                timeout=30.0,  # 30 second timeout
                check_same_thread=False
            )
            conn.row_factory = sqlite3.Row  # Return rows as dictionaries
            
            # Consistent safety/concurrency PRAGMAs (WAL, busy_timeout,
            # synchronous=NORMAL, foreign_keys) shared with the ORM engine.
            _apply_sqlite_pragmas(conn)
                
            return conn
            
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                # Wait with exponential backoff before retrying
                delay = base_delay * (2 ** attempt)
                time.sleep(delay)
                continue
            else:
                # Re-raise if not a lock error or max retries exceeded
                raise
        except Exception as e:
            # Re-raise any other exceptions immediately
            raise

class DatabaseManager:
    def __init__(self, connection_string=None):
        """Initialize the database manager with a connection string
        
        Args:
            connection_string (str, optional): SQLAlchemy connection string, defaults to config
        """
        self.logger = logging.getLogger('text2sql.database')
        self.connection_string = connection_string or DATABASE_URI
        self.logger.info(f"Database manager initialized with connection: {self.connection_string}")
        self.engine = None
        
    def connect(self):
        """Establish database connection"""
        start_time = time.time()
        self.logger.info("Attempting database connection")
        
        try:
            self.engine = create_db_engine(self.connection_string)
            self.logger.info(f"Database connection established in {time.time() - start_time:.2f}s")
            return True
        except Exception as e:
            self.logger.error(f"Database connection error: {str(e)}", exc_info=True)
            print(f"Database connection error: {e}")
            return False
    
    def execute_query(self, sql_query):
        """Execute an SQL query and return results
        
        Args:
            sql_query (str): The SQL query to execute
            
        Returns:
            dict: Dictionary with query results and metadata
        """
        start_time = time.time()
        self.logger.info(f"Executing SQL query: {sql_query}")
        
        if not self.engine:
            self.logger.warning("No active database connection, attempting to connect")
            if not self.connect():
                self.logger.error("Failed to connect to database to execute query")
                return {"error": "Database connection failed", "data": None}
        
        try:
            # Execute the query and fetch results
            with self.engine.connect() as conn:
                self.logger.info("Query execution started")
                execution_start = time.time()
                result = conn.execute(text(sql_query))
                data = result.fetchall()
                execution_time = time.time() - execution_start
                self.logger.info(f"Query execution completed in {execution_time:.2f}s")
                
                # Convert to pandas DataFrame for easier data manipulation
                if data:
                    df = pd.DataFrame(data, columns=result.keys())
                    self.logger.info(f"Query returned {len(df)} rows with {len(df.columns)} columns")
                    
                    # Log sample data (first few rows) at debug level
                    # if len(df) > 0:
                    #     sample_size = min(3, len(df))
                    #     sample_df = df.head(sample_size)
                    #     self.logger.info(f"Sample data (first {sample_size} rows):\n{sample_df.to_string()}")
                    
                    processing_time = time.time() - start_time
                    self.logger.info(f"Query processing completed in {processing_time:.2f}s")
                    
                    return {
                        "success": True,
                        "data": df,
                        "row_count": len(df),
                        "columns": df.columns.tolist(),
                        "error": None,
                        "execution_time": execution_time
                    }
                else:
                    self.logger.info("Query executed successfully but returned no rows")
                    processing_time = time.time() - start_time
                    self.logger.info(f"Query processing completed in {processing_time:.2f}s")
                    
                    return {
                        "success": True,
                        "data": None,
                        "row_count": 0,
                        "columns": result.keys(),
                        "error": None,
                        "execution_time": execution_time
                    }
                    
        except SQLAlchemyError as e:
            processing_time = time.time() - start_time
            error_msg = str(e)
            self.logger.error(f"SQL error after {processing_time:.2f}s: {error_msg}", exc_info=True)
            
            return {
                "success": False,
                "data": None,
                "row_count": 0,
                "columns": [],
                "error": error_msg,
                "execution_time": processing_time
            }
    
    def get_query_examples(self, table_name=None, limit=5):
        """Get example queries for a given table or the database
        
        Args:
            table_name (str, optional): Specific table to get examples for
            limit (int, optional): Maximum number of examples to return
            
        Returns:
            list: Example queries with matching SQL
        """
        self.logger.info(f"Getting query examples for table: {table_name if table_name else 'all tables'}")
        examples = []
        
        # Use schema manager to get table information
        schema_manager = SchemaManager()
        
        # Add table-specific examples if table_name is provided
        if table_name:
            self.logger.info(f"Generating examples for table: {table_name}")
            examples.extend(self._generate_table_examples(table_name, schema_manager))
        else:
            # General database examples based on schema
            self.logger.info("Generating general database examples")
            examples = [
                {
                    "question": "How many tables are in the database?",
                    "sql": "SELECT COUNT(*) AS table_count FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                }
            ]
            
            # Add example using first table found
            tables = schema_manager.get_table_names()
            if tables:
                first_table = tables[0]
                examples.extend([
                    {
                        "question": f"Show me the first 10 rows of the {first_table} table",
                        "sql": f"SELECT * FROM {first_table} LIMIT 10"
                    },
                    {
                        "question": f"Get the total number of records in the {first_table} table",
                        "sql": f"SELECT COUNT(*) AS total_records FROM {first_table}"
                    }
                ])
        
        self.logger.info(f"Generated {len(examples)} example queries (limit: {limit})")
        return examples[:limit]
    
    def _generate_table_examples(self, table_name, schema_manager):
        """Generate example queries for a specific table using schema information
        
        Args:
            table_name (str): Name of the table
            schema_manager (SchemaManager): Instance of schema manager
            
        Returns:
            list: Example queries with matching SQL
        """
        self.logger.info(f"Generating example queries for table: {table_name}")
        examples = [
            {
                "question": f"How many records are in the {table_name} table?",
                "sql": f"SELECT COUNT(*) AS record_count FROM {table_name}"
            },
            {
                "question": f"Show me the first 10 rows of the {table_name} table",
                "sql": f"SELECT * FROM {table_name} LIMIT 10"
            }
        ]
        
        # Get table information from schema
        table = schema_manager.get_table_by_name(table_name)
        if table:
            columns = table["columns"]
            
            if columns:
                # Find a good text column for DISTINCT example (prefer name, description, or type columns)
                text_cols = [col for col in columns if col["datatype"].upper() == "TEXT"]
                name_cols = [col for col in text_cols if any(x in col["name"].lower() for x in ["name", "type", "category", "status"])]
                
                if name_cols:
                    col = name_cols[0]
                    examples.append({
                        "question": f"What are the distinct values of {col['name']} in the {table_name} table?",
                        "sql": f"SELECT DISTINCT {col['name']} FROM {table_name}"
                    })
                
                # Find numeric columns for aggregation examples
                numeric_cols = [col for col in columns if col["datatype"].upper() in ("INTEGER", "REAL", "DECIMAL", "NUMBER")]
                
                # If we have both categorical and numeric columns, create aggregate example
                if text_cols and numeric_cols:
                    group_col = name_cols[0] if name_cols else text_cols[0]
                    agg_col = numeric_cols[0]
                    
                    examples.append({
                        "question": f"Get total {agg_col['name']} grouped by {group_col['name']} in the {table_name} table",
                        "sql": f"SELECT {group_col['name']}, SUM({agg_col['name']}) as total_{agg_col['name']} "
                              f"FROM {table_name} "
                              f"GROUP BY {group_col['name']}"
                    })
                
                # Add example with primary key lookup if available
                primary_keys = [col for col in columns if col.get("is_primary_key")]
                if primary_keys:
                    pk = primary_keys[0]
                    examples.append({
                        "question": f"Get record from {table_name} where {pk['name']} is 1",
                        "sql": f"SELECT * FROM {table_name} WHERE {pk['name']} = 1"
                    })
                
                # If this is orders or similar transactional table, add date-based example
                date_cols = [col for col in columns if "date" in col["name"].lower()]
                if date_cols:
                    date_col = date_cols[0]
                    examples.append({
                        "question": f"Get {table_name} from the last 30 days",
                        "sql": f"SELECT * FROM {table_name} "
                              f"WHERE {date_col['name']} >= date('now', '-30 days')"
                    })
                    
        return examples
    
    def close(self):
        """Close database connections"""
        self.logger.info("Closing database connection")
        if self.engine:
            self.engine.dispose()
            self.logger.info("Database engine disposed")
    
    def save_user_query(self, user_id, query_text, query_name, query_description):
        """Save a user query to the database
        
        Args:
            user_id (int): ID of the user saving the query
            query_text (str): The SQL query text
            query_name (str): Name of the query
            query_description (str): Description of the query
            
        Returns:
            int: ID of the saved query
        """
        self.logger.info(f"Saving user query: '{query_name}'")
        
        try:
            session = get_db_session()
            
            # Create timestamp
            import datetime
            timestamp = datetime.datetime.now()
            
            # Insert query into user_queries table
            result = session.execute(text("""
                INSERT INTO user_queries 
                (user_id, query_name, query_text, description, created_at)
                VALUES (:user_id, :query_name, :query_text, :description, :created_at)
                RETURNING id
            """), {
                'user_id': user_id,
                'query_name': query_name,
                'query_text': query_text,
                'description': query_description,
                'created_at': timestamp
            })
            
            query_id = result.fetchone()[0]
            session.commit()
            
            self.logger.info(f"Query saved with ID: {query_id}")
            return query_id
            
        except Exception as e:
            self.logger.error(f"Error saving user query: {str(e)}")
            if session:
                session.rollback()
            raise
        finally:
            if session:
                session.close()