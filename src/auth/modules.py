"""Canonical module catalog for the application's role-based access control.

Authorization is intentionally module-oriented instead of endpoint-oriented:
users get roles, roles get modules, and every protected surface belongs to a
module.  Adding a new admin/feature area should only require adding one
``ModuleDefinition`` here and decorating the relevant route(s) with
``module_required("<key>")``.

The same catalog drives the React administration navigation so the client can
never drift from server-side enforcement.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

MODULE_PERMISSION_PREFIX = "module:"

ACCESS_READ = "read"
ACCESS_WRITE = "write"
ACCESS_LEVELS = (ACCESS_READ, ACCESS_WRITE)
ACCESS_RANK = {ACCESS_READ: 1, ACCESS_WRITE: 2}

# Safe methods are treated as read operations for every module route. Modules
# with read-like POST actions can widen this via ``module_required(..., read_methods=...)``.
DEFAULT_READ_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


@dataclass(frozen=True)
class ModuleDefinition:
    """A coherent application capability that can be granted to a role."""

    key: str
    label: str
    description: str
    href: str
    icon: str
    group: str = "Administration"
    #: Administrator-only capability. Server-side enforcement is
    #: ``@admin_required()``; this flag lets ``/api/v1/me`` hide the module from
    #: non-admins and lets the role editor stop offering it as a grantable
    #: module (a non-admin grant would only ever be a dead link).
    admin_only: bool = False

    @property
    def permission_name(self) -> str:
        """Write permission row name persisted in the database (legacy alias)."""
        return self.write_permission_name

    @property
    def write_permission_name(self) -> str:
        """Permission row name for full READ/WRITE module access."""
        return f"{MODULE_PERMISSION_PREFIX}{self.key}"

    @property
    def read_permission_name(self) -> str:
        """Permission row name for READ-only module access."""
        return f"{MODULE_PERMISSION_PREFIX}{self.key}:{ACCESS_READ}"

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "href": self.href,
            "icon": self.icon,
            "group": self.group,
            "admin_only": self.admin_only,
        }

    def as_nav_item(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "href": self.href,
            "icon": self.icon,
        }


# Order matters: it is the order shown in the role permission modal and in the
# administration rail.  Keep each entry capability-oriented, not endpoint-bound.
MODULES: tuple[ModuleDefinition, ...] = (
    ModuleDefinition(
        key="dashboard",
        label="Dashboard",
        description="View the operations dashboard and analytics.",
        href="/admin",
        icon="gauge",
    ),
    ModuleDefinition(
        key="agent_studio",
        label="Agent Studio",
        description="Build, publish and manage access to agents and workflows.",
        href="/agent-studio",
        icon="workflow",
    ),
    ModuleDefinition(
        key="observability",
        label="Observability",
        description="View traces, sessions and Arize Phoenix observability data.",
        href="/observability",
        icon="activity",
    ),
    ModuleDefinition(
        key="skills",
        label="Skills",
        description="Manage the reusable skill library.",
        href="/admin/skills",
        icon="book",
    ),
    ModuleDefinition(
        key="mcp_servers",
        label="MCP Servers",
        description="Manage MCP server connections, status and tools.",
        href="/admin/mcp-servers",
        icon="server",
    ),
    ModuleDefinition(
        key="knowledge",
        label="Knowledge",
        description="Manage knowledge documents, tags and ingestion.",
        href="/admin/knowledge",
        icon="library",
    ),
    ModuleDefinition(
        key="vector_db",
        label="Vector DB",
        description="Manage vector database collections and stored data.",
        href="/admin/vector-db",
        icon="database",
        admin_only=True,
    ),
    ModuleDefinition(
        key="database",
        label="Database",
        description="Browse database schemas and run database queries.",
        href="/admin/database",
        icon="terminal",
        admin_only=True,
    ),
    ModuleDefinition(
        key="file_browser",
        label="File Browser",
        description="Browse, edit, upload and download workspace files.",
        href="/admin/file-browser/",
        icon="folder",
        admin_only=True,
    ),
    ModuleDefinition(
        key="hosted_apps",
        label="Hosted Apps",
        description="Import, run and serve externally built applications under /apps/.",
        href="/admin/hosted-apps",
        icon="box",
    ),
    ModuleDefinition(
        key="users",
        label="Users",
        description="Create, edit, deactivate and assign roles to users.",
        href="/admin/users",
        icon="users",
        admin_only=True,
    ),
    ModuleDefinition(
        key="roles",
        label="Roles",
        description="Manage roles and the modules each role can access.",
        href="/admin/roles",
        icon="shield",
        admin_only=True,
    ),
    ModuleDefinition(
        key="llm",
        label="LLM Manager",
        description="Manage LLM provider connections and defaults.",
        href="/admin/config/llm",
        icon="cpu",
    ),
)

MODULE_BY_KEY: dict[str, ModuleDefinition] = {m.key: m for m in MODULES}
MODULE_KEYS: tuple[str, ...] = tuple(m.key for m in MODULES)
MODULE_PERMISSION_NAMES: frozenset[str] = frozenset(
    name for m in MODULES for name in (m.write_permission_name, m.read_permission_name)
)


def get_module(key: str) -> ModuleDefinition | None:
    return MODULE_BY_KEY.get((key or "").strip().lower())


def normalize_module_keys(raw: Iterable[str] | None) -> tuple[str, ...]:
    """Validate raw module keys and return them in catalog order.

    Unknown keys are ignored so stale client payloads cannot accidentally grant
    unintended access.  If a caller supplies only unknown keys the result is an
    empty tuple, which intentionally removes all module access from a role.
    """

    requested = {str(item).strip().lower() for item in (raw or ()) if str(item).strip()}
    return tuple(m.key for m in MODULES if m.key in requested)


def split_module_permission(permission_name: str | None) -> tuple[str, str] | None:
    """Split ``module:<key>`` → write or ``module:<key>:read`` → read."""
    if not permission_name or not permission_name.startswith(MODULE_PERMISSION_PREFIX):
        return None
    rest = permission_name[len(MODULE_PERMISSION_PREFIX):]
    level = ACCESS_WRITE
    if rest.endswith(":" + ACCESS_READ):
        rest = rest[: -(len(ACCESS_READ) + 1)]
        level = ACCESS_READ
    if rest not in MODULE_BY_KEY:
        return None
    return rest, level


def module_key_from_permission(permission_name: str | None) -> str | None:
    """Return the module key encoded in either permission form, else ``None``."""
    parsed = split_module_permission(permission_name)
    return parsed[0] if parsed else None


def module_level_from_permission(permission_name: str | None) -> str | None:
    parsed = split_module_permission(permission_name)
    return parsed[1] if parsed else None


def module_permission_name(key: str, level: str = ACCESS_WRITE) -> str:
    module = MODULE_BY_KEY.get((key or "").strip().lower())
    if module is None:
        raise KeyError(f"Unknown module: {key}")
    if str(level).strip().lower() == ACCESS_READ:
        return module.read_permission_name
    return module.write_permission_name


def _normalize_level(raw) -> str | None:
    value = str(raw or "").strip().lower()
    if value in {"write", "readwrite", "read_write", "rw", "full", "edit"}:
        return ACCESS_WRITE
    if value in {"read", "readonly", "read_only", "ro", "view"}:
        return ACCESS_READ
    return None


def normalize_module_assignments(raw) -> dict[str, str]:
    """Normalise a role payload to ``{module_key: "read"|"write"}``.

    Supported shapes:
      - ``["knowledge", "users"]``                       (legacy => write)
      - ``{"knowledge": "read", "users": "write"}``
      - ``[{"key": "knowledge", "access": "read"}]``
    Unknown modules / levels are ignored.
    """
    requested: dict[str, str] = {}

    def _add(key, level):
        key = str(key or "").strip().lower()
        if key not in MODULE_BY_KEY:
            return
        normalized = _normalize_level(level) or ACCESS_WRITE
        # Write always wins if a caller sends duplicates for the same module.
        if requested.get(key) != ACCESS_WRITE:
            requested[key] = normalized

    if isinstance(raw, dict):
        for key, level in raw.items():
            _add(key, level)
    else:
        for item in raw or ():
            if isinstance(item, str):
                _add(item, ACCESS_WRITE)
            elif isinstance(item, dict):
                level = item.get("access") or item.get("level") or item.get("access_level")
                _add(item.get("key") or item.get("module"), level)

    # Return in catalog order for stable UI/tests.
    return {m.key: requested[m.key] for m in MODULES if m.key in requested}


def level_allows(actual_level: str | None, required_level: str = ACCESS_READ) -> bool:
    if actual_level not in ACCESS_RANK:
        return False
    required = required_level if required_level in ACCESS_RANK else ACCESS_READ
    return ACCESS_RANK[actual_level] >= ACCESS_RANK[required]


def nav_modules() -> list[ModuleDefinition]:
    return list(MODULES)
