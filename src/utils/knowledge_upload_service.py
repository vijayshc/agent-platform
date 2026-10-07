"""Upload and inspection services for knowledge documents and tabular data.

Extracted from knowledge_routes.py to keep file inspection, MIME resolution,
and form parsing decoupled from HTTP routing.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any
from werkzeug.utils import secure_filename

from src.utils import tabular_ingest

logger = logging.getLogger("text2sql.knowledge_upload")


#: Extensions the platform hands back with a real content type. Everything else
#: downloads as octet-stream: a user-supplied filename must never decide that
#: the browser renders active content (HTML, SVG) on the platform's own origin.
_DOCUMENT_MIME_TYPES = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "txt": "text/plain",
    "csv": "text/csv",
}


def document_mimetype(content_type: str | None) -> str:
    """Content type for a stored document, keyed by its stored extension."""
    return _DOCUMENT_MIME_TYPES.get(
        (content_type or "").lower().strip("."), "application/octet-stream"
    )


def inspect_uploaded_file(file_storage: Any) -> tuple[dict[str, Any] | None, str | None, int]:
    """Describe a CSV/Excel upload's columns and preview before it is indexed.

    Returns:
        (result_dict, error_message, status_code)
    """
    if not file_storage or not file_storage.filename:
        return None, "No selected document", 400

    original_filename = secure_filename(file_storage.filename)
    _, ext = os.path.splitext(original_filename)
    content_type = ext.lower().strip(".")
    if not tabular_ingest.is_tabular(content_type):
        return {"kind": "document", "content_type": content_type}, None, 200

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{content_type}") as temp_file:
            file_storage.save(temp_file)
            temp_path = temp_file.name
        info = tabular_ingest.inspect_table(temp_path, content_type)
        return {"kind": "tabular", "content_type": content_type, **info}, None, 200
    except Exception as exc:
        logger.error("Error inspecting document %s: %s", original_filename, exc, exc_info=True)
        return None, f"Could not read file: {exc}", 400
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def parse_tags_from_form(form_data: Any) -> list[str]:
    """Parse comma-separated tags from form data."""
    tags_string = form_data.get("tags", "") if hasattr(form_data, "get") else ""
    if not tags_string:
        return []
    return [tag.strip() for tag in tags_string.split(",") if tag.strip()]


def parse_allowed_roles_from_form(form_data: Any) -> list[str]:
    """Parse allowed roles list or comma-separated string from form data."""
    if not hasattr(form_data, "getlist"):
        return []
    roles_list = form_data.getlist("allowed_roles")
    if len(roles_list) > 1:
        return roles_list
    if len(roles_list) == 1:
        val = roles_list[0]
        if "," in val:
            return [role.strip() for role in val.split(",") if role.strip()]
        return [val] if val.strip() else []
    return []
