"""Admin-only enforcement for the file browser.

Real integration: the shipped ``admin_file_browser_bp`` runs on a minimal Flask
app with the *real* ``admin_required`` / ``module_required`` decorators, the
real ``UserManager`` + ``resource_access`` code paths against a temp SQLAlchemy
database, and the real ``FileBrowserService`` rooted at a temp directory.
Nothing here mocks the authorization decisions.
"""

from __future__ import annotations

import io
import sqlite3
import zipfile
from pathlib import Path

import pytest
from flask import Blueprint, Flask
from sqlalchemy.orm import scoped_session, sessionmaker
from werkzeug.datastructures import FileStorage

from src.auth import resource_access
from src.models.user import Base, Permission, Role, User
from src.services.file_browser_service import FileBrowserService
from src.utils import database
from src.utils.user_manager import UserManager

REPO_ROOT = Path(__file__).resolve().parents[1]
BROWSE_URL = "/admin/file-browser"
PAGE_URL = "/admin/file-browser/"


def _raw_connect(db_path: Path):
    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn

    return _connect


@pytest.fixture()
def identities(tmp_path, monkeypatch):
    """Real ORM users/roles/permissions in a temp SQLite database."""
    db_path = tmp_path / "auth.db"
    engine = database.create_db_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    session_factory = scoped_session(sessionmaker(bind=engine))
    monkeypatch.setattr(database, "_Session", session_factory)
    monkeypatch.setattr("src.agent_platform.db.get_db_connection", _raw_connect(db_path))
    resource_access._reset_schema_guard()

    admin_role = Role(name="admin")
    analyst_role = Role(name="analyst", permissions=[Permission(name="module:file_browser")])
    admin_user = User(
        username="root", email="root@example.com", password_hash="x", roles=[admin_role]
    )
    analyst_user = User(
        username="analyst", email="analyst@example.com", password_hash="x", roles=[analyst_role]
    )
    session = session_factory()
    session.add_all([admin_user, analyst_user])
    session.commit()
    ids = {"admin": admin_user.id, "analyst": analyst_user.id}

    try:
        yield ids
    finally:
        session_factory.remove()
        engine.dispose()
        resource_access._reset_schema_guard()


@pytest.fixture()
def client(identities, tmp_path, monkeypatch):
    browse_root = tmp_path / "workspace"
    browse_root.mkdir()
    (browse_root / "readme.txt").write_text("hello admin", encoding="utf-8")
    service = FileBrowserService(
        root_path=str(browse_root), allowed_extensions={".txt", ".json", ".py"}
    )
    # The route module imports the singleton at import time; swap in the same
    # real service bound to a temp root. No real workspace file is touched.
    monkeypatch.setattr(
        "src.routes.admin_file_browser_routes.file_browser_service", service
    )

    app = Flask(__name__, template_folder=str(REPO_ROOT / "templates"))
    app.secret_key = "file-browser-test"
    app.jinja_env.globals["csrf_token"] = lambda: "test-csrf"

    from src.routes.admin_file_browser_routes import admin_file_browser_bp

    app.register_blueprint(admin_file_browser_bp)

    @app.route("/")
    def index():
        return "index"

    auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

    @auth_bp.route("/login")
    def login():
        return "login"

    app.register_blueprint(auth_bp)

    flask_client = app.test_client()
    flask_client.browse_root = browse_root  # type: ignore[attr-defined]
    flask_client.service = service  # type: ignore[attr-defined]
    return flask_client


def _login(flask_client, user_id: int):
    with flask_client.session_transaction() as session:
        session["user_id"] = user_id


# ---------------------------------------------------------------------------
# fixture sanity: the analyst really does hold module:file_browser write
# ---------------------------------------------------------------------------


def test_analyst_holds_file_browser_write_but_is_not_admin(identities, client):
    from src.auth.resource_access import is_admin

    analyst_id = identities["analyst"]
    assert UserManager().has_module_access(analyst_id, "file_browser", min_level="write")
    assert not is_admin(analyst_id)
    assert is_admin(identities["admin"])


# ---------------------------------------------------------------------------
# every shipped view carries both markers
# ---------------------------------------------------------------------------


def test_every_route_is_marked_admin_and_module(client):
    from src.auth.decorators import ADMIN_REQUIRED_ATTR, REQUIRED_MODULES_ATTR

    views = {
        name: fn
        for name, fn in client.application.view_functions.items()
        if name.startswith("admin_file_browser.")
    }
    assert len(views) == 9
    for name, view in views.items():
        assert getattr(view, ADMIN_REQUIRED_ATTR, False), name
        assert "file_browser" in getattr(view, REQUIRED_MODULES_ATTR, ()), name


# ---------------------------------------------------------------------------
# unauthenticated
# ---------------------------------------------------------------------------


def test_unauthenticated_api_requests_are_401(client):
    assert client.get(f"{BROWSE_URL}/api/list", content_type="application/json").status_code == 401
    assert client.put(
        f"{BROWSE_URL}/api/file-content",
        json={"path": "readme.txt", "content": "x"},
    ).status_code == 401
    assert client.delete(f"{BROWSE_URL}/api/item", json={"path": "readme.txt"}).status_code == 401
    upload = client.post(
        f"{BROWSE_URL}/api/upload-file",
        data={"path": "", "file": (io.BytesIO(b"hi"), "new.txt")},
        content_type="multipart/form-data",
    )
    # Every ``/api/`` route is an API surface even for multipart posts: denial is
    # JSON here, not a browser redirect.
    assert upload.status_code == 401
    assert upload.is_json


def test_unauthenticated_browser_page_redirects_to_login(client):
    response = client.get(PAGE_URL)
    assert response.status_code == 302
    assert "/auth/login" in response.headers["Location"]


# ---------------------------------------------------------------------------
# authenticated non-admin holding module:file_browser write
# ---------------------------------------------------------------------------


def test_non_admin_denied_on_json_endpoints(identities, client):
    _login(client, identities["analyst"])
    headers = {"Content-Type": "application/json"}

    assert client.get(f"{BROWSE_URL}/api/list", headers=headers).status_code == 403
    assert client.get(
        f"{BROWSE_URL}/api/file-content?path=readme.txt", headers=headers
    ).status_code == 403
    assert client.put(
        f"{BROWSE_URL}/api/file-content",
        json={"path": "readme.txt", "content": "owned"},
    ).status_code == 403
    assert client.delete(f"{BROWSE_URL}/api/item", json={"path": "readme.txt"}).status_code == 403
    assert client.get(
        f"{BROWSE_URL}/api/download-file?path=readme.txt", headers=headers
    ).status_code == 403
    assert client.get(
        f"{BROWSE_URL}/api/download-folder?path=", headers=headers
    ).status_code == 403


def test_non_admin_uploads_and_page_are_denied_without_writing(identities, client):
    _login(client, identities["analyst"])
    browse_root: Path = client.browse_root

    upload = client.post(
        f"{BROWSE_URL}/api/upload-file",
        data={"path": "", "file": (io.BytesIO(b"pwned"), "evil.txt")},
        content_type="multipart/form-data",
    )
    # ``/admin/file-browser/api/...`` is an API surface: an authenticated
    # non-admin gets 403 JSON (never the page-style flash redirect).
    assert upload.status_code == 403
    assert upload.is_json
    assert not (browse_root / "evil.txt").exists()

    folder = client.post(
        f"{BROWSE_URL}/api/upload-folder",
        data={
            "path": "",
            "files": (io.BytesIO(b"pwned"), "evil.txt"),
            "relative_paths": "nested/evil.txt",
        },
        content_type="multipart/form-data",
    )
    assert folder.status_code == 403
    assert not (browse_root / "nested").exists()

    page = client.get(PAGE_URL)
    assert page.status_code == 302
    assert page.headers["Location"].endswith("/")

    # The file the analyst tried to overwrite is untouched.
    assert (browse_root / "readme.txt").read_text(encoding="utf-8") == "hello admin"


# ---------------------------------------------------------------------------
# admin is allowed
# ---------------------------------------------------------------------------


def test_admin_can_use_the_file_browser(identities, client):
    _login(client, identities["admin"])

    listing = client.get(f"{BROWSE_URL}/api/list", headers={"Content-Type": "application/json"})
    assert listing.status_code == 200
    assert [item["name"] for item in listing.get_json()["items"]] == ["readme.txt"]

    assert client.get(
        f"{BROWSE_URL}/api/file-content?path=readme.txt",
        headers={"Content-Type": "application/json"},
    ).status_code == 200

    upload = client.post(
        f"{BROWSE_URL}/api/upload-file",
        data={"path": "", "file": (io.BytesIO(b"added"), "added.txt")},
        content_type="multipart/form-data",
    )
    assert upload.status_code == 200
    assert (client.browse_root / "added.txt").read_text(encoding="utf-8") == "added"

    update = client.put(
        f"{BROWSE_URL}/api/file-content",
        json={"path": "added.txt", "content": "edited"},
    )
    assert update.status_code == 200
    assert (client.browse_root / "added.txt").read_text(encoding="utf-8") == "edited"

    download = client.get(f"{BROWSE_URL}/api/download-file?path=added.txt")
    assert download.status_code == 200

    folder_zip = client.get(f"{BROWSE_URL}/api/download-folder?path=")
    assert folder_zip.status_code == 200
    assert folder_zip.mimetype == "application/zip"
    with zipfile.ZipFile(io.BytesIO(folder_zip.data)) as archive:
        assert "root/added.txt" in archive.namelist()

    delete = client.delete(f"{BROWSE_URL}/api/item", json={"path": "added.txt"})
    assert delete.status_code == 200
    assert not (client.browse_root / "added.txt").exists()

    page = client.get(PAGE_URL)
    assert page.status_code == 200
    assert b'id="agent-app-root"' in page.data


# ---------------------------------------------------------------------------
# path traversal
# ---------------------------------------------------------------------------


def test_path_traversal_is_rejected(identities, client, tmp_path):
    _login(client, identities["admin"])
    browse_root: Path = client.browse_root
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    headers = {"Content-Type": "application/json"}
    assert client.get(
        f"{BROWSE_URL}/api/list?path=../../..", headers=headers
    ).status_code == 400
    assert client.get(
        f"{BROWSE_URL}/api/file-content?path=../../../outside.txt", headers=headers
    ).status_code == 400
    assert client.delete(
        f"{BROWSE_URL}/api/item", json={"path": "../outside.txt"}
    ).status_code == 400
    assert outside.read_text(encoding="utf-8") == "secret"

    # Uploads cannot escape: neither the target directory nor the client-supplied
    # multipart filename may point outside the browse root.
    target_escape = client.post(
        f"{BROWSE_URL}/api/upload-file",
        data={"path": "..", "file": (io.BytesIO(b"x"), "escape.txt")},
        content_type="multipart/form-data",
    )
    assert target_escape.status_code == 400

    name_escape = client.post(
        f"{BROWSE_URL}/api/upload-file",
        data={"path": "", "file": (io.BytesIO(b"x"), "../escape.txt")},
        content_type="multipart/form-data",
    )
    assert name_escape.status_code == 400
    absolute_name = client.post(
        f"{BROWSE_URL}/api/upload-file",
        data={"path": "", "file": (io.BytesIO(b"x"), "/tmp/escape.txt")},
        content_type="multipart/form-data",
    )
    assert absolute_name.status_code == 400

    folder_escape = client.post(
        f"{BROWSE_URL}/api/upload-folder",
        data={
            "path": "",
            "files": (io.BytesIO(b"x"), "escape.txt"),
            "relative_paths": "../escape.txt",
        },
        content_type="multipart/form-data",
    )
    assert folder_escape.status_code == 400

    assert not (tmp_path / "escape.txt").exists()
    assert not (browse_root.parent / "escape.txt").exists()


def test_service_rejects_symlink_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    (root / "link.txt").symlink_to(secret)
    (root / "linkdir").symlink_to(tmp_path, target_is_directory=True)
    (root / "inside.txt").write_text("inside", encoding="utf-8")
    (root / "sub").mkdir()
    (root / "sub" / "escape.txt").symlink_to(secret)

    service = FileBrowserService(root_path=str(root), allowed_extensions={".txt"})
    with pytest.raises(ValueError):
        service.get_file_content("link.txt")
    with pytest.raises(ValueError):
        service.list_directory("linkdir")
    with pytest.raises(ValueError):
        service.save_uploaded_file(
            "linkdir", FileStorage(stream=io.BytesIO(b"x"), filename="a.txt"), 1
        )
    assert secret.read_text(encoding="utf-8") == "secret"
    assert not (tmp_path / "a.txt").exists()

    # A folder download must not copy a symlink's out-of-root target bytes.
    buffer, _ = service.stream_folder_as_zip("")
    with zipfile.ZipFile(buffer) as archive:
        names = archive.namelist()
        contents = b"".join(archive.read(n) for n in names if not n.endswith("/"))
    assert "root/link.txt" not in names
    assert "root/sub/escape.txt" not in names
    assert b"secret" not in contents
    assert "root/inside.txt" in names
