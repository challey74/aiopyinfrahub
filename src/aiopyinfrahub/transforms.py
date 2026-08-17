"""Transforms: server-side Python and Jinja2 rendering."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiopyinfrahub.graphql import segment

if TYPE_CHECKING:
    from aiopyinfrahub.api import Api


class Transforms:
    """ih.transforms: render a transform stored in a git repository.

    Both routes run the transform's own GraphQL query server-side and
    feed the result to the transform, so the extra keyword arguments
    here become that query's variables. Nothing in this client
    interprets them; they ride along as query parameters.
    """

    def __init__(self, api: Api) -> None:
        self.api = api

    def _params(
        self, branch: str | None, at: str | None, params: dict[str, Any]
    ) -> dict[str, Any]:
        query = dict(params)
        resolved = branch if branch is not None else self.api.branch
        if resolved:
            query["branch"] = resolved
        if at:
            query["at"] = at
        return query

    async def render_python(
        self,
        transform_id: str,
        *,
        branch: str | None = None,
        at: str | None = None,
        **params: Any,
    ) -> Any:
        """Run a Python transform and return its output, decoded.

        Args:
            transform_id: The CoreTransformPython's id or name.
            branch: Branch to render on, overriding the client default.
            at: Timestamp to render the graph as of.
            **params: Variables for the transform's GraphQL query. Two
                names are taken: `branch` and `at` bind to the arguments
                above rather than reaching the server as variables.

        Returns:
            Whatever the transform returned, decoded from JSON: these
            transforms produce data structures, not text.
        """
        return await self.api._request(
            "GET",
            f"{self.api.base_url}/api/transform/python/{segment(transform_id)}",
            params=self._params(branch, at, params),
        )

    async def render_jinja2(
        self,
        transform_id: str,
        *,
        branch: str | None = None,
        at: str | None = None,
        **params: Any,
    ) -> str:
        """Run a Jinja2 transform and return the rendered text.

        See render_python() for the arguments. This route answers
        text/plain, so the body is the render itself rather than a JSON
        document wrapping it, and it is returned as a str.
        """
        resp = await self.api._request_response(
            "GET",
            f"{self.api.base_url}/api/transform/jinja2/{segment(transform_id)}",
            params=self._params(branch, at, params),
        )
        return resp.text
