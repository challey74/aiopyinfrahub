import hashlib
import json

import pytest
from conftest import FILE_NODE_ID

import aiopyinfrahub

HFID = ["configs", "sw-1.cfg"]


async def test_upload_and_get_round_trip(ih):
    result = await ih.storage.upload("hello infrahub")
    assert result["checksum"] == hashlib.md5(b"hello infrahub").hexdigest()
    assert await ih.storage.get(result["identifier"]) == b"hello infrahub"


async def test_get_answers_bytes_not_json(ih, fake):
    """An object is a file; nothing here decodes it."""
    identifier = fake.add_storage_object(b"\x00\x01not json")["identifier"]
    assert await ih.storage.get(identifier) == b"\x00\x01not json"


async def test_upload_posts_the_content_route(ih, fake):
    await ih.storage.upload("x")
    request = fake.requests[-1]
    assert request.url.path == "/api/storage/upload/content"
    assert json.loads(request.content) == {"content": "x"}


async def test_upload_file_sends_multipart(ih, fake, tmp_path):
    path = tmp_path / "startup.cfg"
    path.write_bytes(b"hostname sw-1\n")
    result = await ih.storage.upload_file(path)
    request = fake.requests[-1]
    assert request.url.path == "/api/storage/upload/file"
    assert request.headers["Content-Type"].startswith("multipart/form-data")
    assert fake.uploads[-1]["file"] == b"hostname sw-1\n"
    assert await ih.storage.get(result["identifier"]) == b"hostname sw-1\n"


async def test_upload_file_accepts_a_string_path(ih, tmp_path):
    path = tmp_path / "banner.txt"
    path.write_text("authorized use only")
    result = await ih.storage.upload_file(str(path))
    assert await ih.storage.get(result["identifier"]) == b"authorized use only"


async def test_get_file_by_node_id(ih, fake):
    fake.add_file(FILE_NODE_ID, "CoreFileObject", HFID, b"one")
    assert await ih.storage.get_file(FILE_NODE_ID) == b"one"
    assert fake.requests[-1].url.path == f"/api/storage/files/{FILE_NODE_ID}"


async def test_get_file_by_storage_id(ih, fake):
    identifier = fake.add_file(FILE_NODE_ID, "CoreFileObject", HFID, b"two")
    assert await ih.storage.get_file_by_storage_id(identifier) == b"two"
    path = f"/api/storage/files/by-storage-id/{identifier}"
    assert fake.requests[-1].url.path == path


async def test_get_file_by_hfid_repeats_the_parameter(ih, fake):
    fake.add_file(FILE_NODE_ID, "CoreFileObject", HFID, b"three")
    content = await ih.storage.get_file_by_hfid("CoreFileObject", HFID)
    assert content == b"three"
    url = fake.requests[-1].url
    assert url.path == "/api/storage/files/by-hfid/CoreFileObject"
    assert url.params.get_list("hfid") == HFID


async def test_get_file_by_a_wrong_hfid_is_a_404(ih, fake):
    fake.add_file(FILE_NODE_ID, "CoreFileObject", HFID, b"three")
    with pytest.raises(aiopyinfrahub.RequestError) as excinfo:
        await ih.storage.get_file_by_hfid("CoreFileObject", ["configs"])
    assert excinfo.value.status_code == 404


async def test_file_objects_refuse_the_plain_object_route(ih, fake):
    """The server answers 403 there and points at the file routes."""
    identifier = fake.add_file(FILE_NODE_ID, "CoreFileObject", HFID, b"four")
    with pytest.raises(aiopyinfrahub.RequestError) as excinfo:
        await ih.storage.get(identifier)
    assert excinfo.value.status_code == 403
    assert await ih.storage.get_file_by_storage_id(identifier) == b"four"


async def test_an_unknown_identifier_raises(ih):
    with pytest.raises(aiopyinfrahub.RequestError) as excinfo:
        await ih.storage.get("no-such-object")
    assert excinfo.value.status_code == 404


async def test_identifiers_are_percent_encoded(ih, fake):
    """Identifiers are caller data landing in a path segment."""
    with pytest.raises(aiopyinfrahub.RequestError):
        await ih.storage.get("a/b c")
    assert "/api/storage/object/a%2Fb%20c" in str(fake.requests[-1].url)


async def test_the_kind_segment_is_percent_encoded(ih, fake):
    with pytest.raises(aiopyinfrahub.RequestError):
        await ih.storage.get_file_by_hfid("Core FileObject", ["x"])
    assert "/api/storage/files/by-hfid/Core%20FileObject" in str(fake.requests[-1].url)


async def test_uploads_carry_the_auth_header(ih, fake, tmp_path):
    """Uploads go through the request core, not around it."""
    path = tmp_path / "a.txt"
    path.write_text("x")
    await ih.storage.upload_file(path)
    assert fake.requests[-1].headers["X-INFRAHUB-KEY"] == "abc123"
