"""Agent authorization: a user (and their API keys) may only run agents they can access.

Mirrors the Agent Studio "Access" chips: admin, owner, or a granted role.
"""

from __future__ import annotations

from types import SimpleNamespace

from src.agent_platform.api.api_helpers import user_can_access_definition
from src.agent_platform.catalog.store import DefinitionStore


def test_owner_can_access(monkeypatch):
    monkeypatch.setattr("src.utils.user_manager.UserManager.has_role", lambda self, uid, role: False)
    assert user_can_access_definition({"id": 1, "created_by": 7}, 7)


def test_admin_can_access_any(monkeypatch):
    monkeypatch.setattr(
        "src.utils.user_manager.UserManager.has_role",
        lambda self, uid, role: role == "admin",
    )
    assert user_can_access_definition({"id": 1, "created_by": 999}, 5)


def test_non_owner_without_roles_is_denied(monkeypatch):
    monkeypatch.setattr("src.utils.user_manager.UserManager.has_role", lambda self, uid, role: False)
    monkeypatch.setattr(DefinitionStore, "list_access", classmethod(lambda cls, did: []))
    assert not user_can_access_definition({"id": 1, "created_by": 999}, 5)


def test_granted_role_can_access(monkeypatch):
    monkeypatch.setattr("src.utils.user_manager.UserManager.has_role", lambda self, uid, role: False)
    monkeypatch.setattr(
        DefinitionStore,
        "list_access",
        classmethod(lambda cls, did: [{"role_id": 3, "role_name": "analyst"}]),
    )
    monkeypatch.setattr(
        "src.utils.user_manager.UserManager.get_user_by_id",
        lambda self, uid: SimpleNamespace(roles=[SimpleNamespace(id=3)]),
    )
    assert user_can_access_definition({"id": 1, "created_by": 999}, 5)


def test_unrelated_role_is_denied(monkeypatch):
    monkeypatch.setattr("src.utils.user_manager.UserManager.has_role", lambda self, uid, role: False)
    monkeypatch.setattr(
        DefinitionStore,
        "list_access",
        classmethod(lambda cls, did: [{"role_id": 3, "role_name": "analyst"}]),
    )
    monkeypatch.setattr(
        "src.utils.user_manager.UserManager.get_user_by_id",
        lambda self, uid: SimpleNamespace(roles=[SimpleNamespace(id=9)]),
    )
    assert not user_can_access_definition({"id": 1, "created_by": 999}, 5)


def test_unauthenticated_is_denied():
    assert not user_can_access_definition({"id": 1, "created_by": None}, None)
