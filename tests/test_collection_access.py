"""Access control for vector collections.

Collections bridge to the shared ``resource_access`` store through a name ->
integer registry.  These tests pin the two properties that matter: grants are
stored/decided by the shared foundation, and access is deny-by-default for
non-administrators.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.auth import resource_access
from src.utils import collection_access

_ADMIN, _ANALYST, _OTHER = 1, 11, 13
ROLE_ADMIN, ROLE_ANALYST, ROLE_OTHER = 1, 3, 4

_SCHEMA = """
CREATE TABLE IF NOT EXISTS roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    description TEXT
);
CREATE TABLE IF NOT EXISTS user_roles (
    user_id INTEGER,
    role_id INTEGER
);
"""


@pytest.fixture()
def coll(temp_db):
    conn = sqlite3.connect(str(temp_db))
    try:
        conn.executescript(_SCHEMA)
        for role_id, name in ((ROLE_ADMIN, "admin"), (ROLE_ANALYST, "analyst"), (ROLE_OTHER, "other")):
            conn.execute("INSERT INTO roles (id, name) VALUES (?, ?)", (role_id, name))
        for user_id, role_id in ((_ADMIN, ROLE_ADMIN), (_ANALYST, ROLE_ANALYST), (_OTHER, ROLE_OTHER)):
            conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES (?, ?)", (user_id, role_id))
        conn.commit()
    finally:
        conn.close()

    resource_access._reset_schema_guard()
    collection_access._reset_schema_guard()
    resource_access.ensure_tables()
    yield temp_db
    resource_access._reset_schema_guard()
    collection_access._reset_schema_guard()


def test_unregistered_collection_is_admin_only(coll):
    assert collection_access.visible_collection_names(["alpha"], _ANALYST) == []
    assert collection_access.visible_collection_names(["alpha"], _ADMIN) == ["alpha"]
    assert not collection_access.can_access_collection("alpha", _ANALYST)
    assert collection_access.can_access_collection("alpha", _ADMIN)


def test_registration_claims_owner_and_uses_shared_store(coll):
    entry = collection_access.register_collection("alpha", _ADMIN)
    assert entry["access_id"] is not None
    # The shared foundation owns the name -> id resolution and the grant table.
    assert resource_access.is_registered(collection_access.RESOURCE_TYPE)
    assert resource_access.owner_of(collection_access.RESOURCE_TYPE, entry["access_id"]) == _ADMIN


def test_grant_makes_collection_visible_to_role(coll):
    entry = collection_access.register_collection("alpha", _ADMIN)
    assert collection_access.visible_collection_names(["alpha"], _ANALYST) == []

    resource_access.set_access(
        collection_access.RESOURCE_TYPE, entry["access_id"], [ROLE_ANALYST], granted_by=_ADMIN
    )

    assert collection_access.visible_collection_names(["alpha"], _ANALYST) == ["alpha"]
    assert collection_access.visible_collection_names(["alpha"], _OTHER) == []
    assert collection_access.can_access_collection("alpha", _ANALYST)
    assert not collection_access.can_access_collection("alpha", _OTHER)
    assert collection_access.granted_role_names("alpha") == ["analyst"]


def test_visibility_preserves_input_order_and_filters(coll):
    collection_access.register_collection("beta", _ADMIN)
    entry = collection_access.register_collection("alpha", _ADMIN)
    resource_access.set_access(
        collection_access.RESOURCE_TYPE, entry["access_id"], [ROLE_ANALYST], granted_by=_ADMIN
    )
    assert collection_access.visible_collection_names(["alpha", "beta", "gamma"], _ANALYST) == ["alpha"]


def test_registration_requires_an_owner(coll):
    with pytest.raises(ValueError):
        collection_access.register_collection("alpha", None)


def test_register_missing_never_changes_an_existing_owner(coll):
    first = collection_access.register_collection("alpha", _ADMIN)
    collection_access.register_missing(["alpha", "beta"], _OTHER)
    assert collection_access.registry_rows(["alpha"])["alpha"]["owner_id"] == _ADMIN
    assert collection_access.registry_rows(["beta"])["beta"]["owner_id"] == _OTHER
    assert first["access_id"] != collection_access.access_id_for("beta")
