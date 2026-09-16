"""Single DB access point so tests can patch one import."""

from src.utils.database import get_db_connection

__all__ = ["get_db_connection"]
