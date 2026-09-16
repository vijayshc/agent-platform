import io
import shutil
from pathlib import Path

import pytest
from werkzeug.datastructures import FileStorage

from src.services.file_browser_service import FileBrowserService


@pytest.fixture()
def temp_root(tmp_path_factory):
    repo_root = Path(__file__).resolve().parents[1]
    base_temp = repo_root / "temp" / "test_file_browser_service"
    base_temp.mkdir(parents=True, exist_ok=True)
    temp_dir = base_temp / tmp_path_factory.mktemp("case").name
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        yield temp_dir
    finally:
        if temp_dir.exists():
            shutil.rmtree(temp_dir)


def make_filestorage(filename: str, data: bytes) -> FileStorage:
    return FileStorage(stream=io.BytesIO(data), filename=filename)


def test_save_and_list_file(temp_root):
    service = FileBrowserService(root_path=str(temp_root), allowed_extensions={".txt"})
    storage = make_filestorage("example.txt", b"sample data")

    saved_path = service.save_uploaded_file("", storage, user_id=1)
    assert saved_path == "example.txt"

    listing = service.list_directory("")
    assert any(item["name"] == "example.txt" for item in listing["items"])
    content = service.get_file_content("example.txt")
    assert content == "sample data"


def test_reject_disallowed_extension(temp_root):
    service = FileBrowserService(root_path=str(temp_root), allowed_extensions={".txt"})
    storage = make_filestorage("malware.exe", b"binary")

    with pytest.raises(ValueError):
        service.save_uploaded_file("", storage, user_id=1)


def test_folder_upload_and_delete(temp_root):
    service = FileBrowserService(root_path=str(temp_root), allowed_extensions={".txt", ".json"})

    files = [
        make_filestorage("readme.txt", b"hello"),
        make_filestorage("config.json", b"{}"),
    ]
    relative_paths = ["folder/readme.txt", "folder/config.json"]

    stored_paths = service.save_uploaded_folder("", files, relative_paths, user_id=2)
    assert set(stored_paths) == {"folder/readme.txt", "folder/config.json"}

    listing = service.list_directory("folder")
    assert len(listing["items"]) == 2

    service.delete_path("folder")
    with pytest.raises(FileNotFoundError):
        service.list_directory("folder")
