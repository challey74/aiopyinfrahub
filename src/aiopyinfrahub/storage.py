"""Storage: Infrahub's object store, and CoreFileObject downloads."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiopyinfrahub.graphql import segment

if TYPE_CHECKING:
    from aiopyinfrahub.api import Api


class Storage:
    """ih.storage: read and write raw objects in Infrahub's object store.

    Everything here is bytes rather than parsed JSON: an object is a
    file, and the server answers with whatever content type it was
    stored under. The store is not branch-aware, since an object is
    addressed by its identifier and not by a node; the file routes are
    branch-aware server-side, resolved from the node they are given.
    """

    def __init__(self, api: Api) -> None:
        self.api = api

    def _url(self, *parts: str) -> str:
        # Identifiers, node ids and kinds are all caller data landing in
        # a path, and httpx2 escapes query parameters but not the path.
        joined = "/".join(segment(part) for part in parts)
        return f"{self.api.base_url}/api/storage/{joined}"

    async def _content(self, url: str, params: dict[str, Any] | None = None) -> bytes:
        # _request_response rather than _request: these routes answer with
        # the file, which is not JSON and must not be decoded as any.
        resp = await self.api._request_response("GET", url, params=params)
        return resp.content

    async def get(self, identifier: str) -> bytes:
        """One object from the store, by its storage identifier.

        Raises:
            RequestError: With status 403 when a CoreFileObject owns that
                identifier. Those are read through
                get_file_by_storage_id() instead, which resolves the
                owning node's permissions first.
        """
        return await self._content(self._url("object", identifier))

    async def upload(self, content: str) -> dict[str, Any]:
        """Store a string as a new object.

        Returns:
            `{"identifier": ..., "checksum": ...}`; the checksum is the
            content's MD5, which is what Infrahub stores objects under.
        """
        return await self.api._request(
            "POST",
            f"{self.api.base_url}/api/storage/upload/content",
            json={"content": content},
            idempotent=False,
        )

    async def upload_file(self, path: str | Path) -> dict[str, Any]:
        """Store a file's contents as a new object.

        The route takes one multipart field, so the file is read into
        memory before it is sent rather than streamed.

        Args:
            path: The local file to upload. Its name travels with the
                part; the server keys the object by checksum regardless.

        Returns:
            The same `{"identifier": ..., "checksum": ...}` upload() does.
        """
        path = Path(path)
        resp = await self.api._request_response(
            "POST",
            f"{self.api.base_url}/api/storage/upload/file",
            files={"file": (path.name, path.read_bytes())},
            idempotent=False,
        )
        return self.api._decode(resp)

    async def get_file(self, node_id: str) -> bytes:
        """A CoreFileObject's content, addressed by the node's id."""
        return await self._content(self._url("files", node_id))

    async def get_file_by_storage_id(self, storage_id: str) -> bytes:
        """A CoreFileObject's content, addressed by its storage id.

        This is the route for an identifier that get() answers 403 for:
        it finds the owning node and checks permissions against it.
        """
        return await self._content(self._url("files", "by-storage-id", storage_id))

    async def get_file_by_hfid(self, kind: str, hfid: list[str]) -> bytes:
        """A CoreFileObject's content, addressed by kind and hfid.

        Args:
            kind: The file object's kind, which is a path segment here
                rather than a filter.
            hfid: The node's human-friendly id, one part per element.
        """
        return await self._content(
            self._url("files", "by-hfid", kind),
            # `hfid` is repeatable: one parameter per part of the id,
            # which is how httpx2 renders a list-valued parameter.
            {"hfid": hfid},
        )
