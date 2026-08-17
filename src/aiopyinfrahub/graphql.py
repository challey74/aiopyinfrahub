"""GraphQL client and renderer for Infrahub's /graphql endpoint."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode

from aiopyinfrahub.exceptions import GraphQLError, RequestError

if TYPE_CHECKING:
    from aiopyinfrahub.api import Api

# Kind names, field names and filter keys are rendered into the query text
# verbatim, so each is checked against GraphQL's Name grammar first. That
# check plus json.dumps on every value is what closes the injection surface
# without promoting anything to a declared variable.
IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

INDENT = "    "


def _identifier(name: Any, what: str) -> str:
    """Check that a name can be rendered into query text as-is."""
    if not isinstance(name, str) or not IDENTIFIER.match(name):
        raise ValueError(f"{what} {name!r} is not a valid GraphQL identifier")
    return name


def segment(value: str) -> str:
    """Percent-encode a value used as a URL path segment.

    Every caller value that lands in a path goes through here: branch
    names, storage identifiers, file-object ids and kinds, artifact and
    transform ids, stored-query names. Nothing else escapes it: httpx
    quotes query parameters but leaves the path alone. Slashes are
    encoded too, unlike in the sister libraries, because no Infrahub path
    segment is allowed to span several.
    """
    return quote(str(value), safe="")


class EnumValue(str):
    """A string rendered as a bare GraphQL enum token, not a quoted literal.

    Infrahub declares a few filters as enums (`InfrahubTask(state: [StateType])`),
    and graphene rejects a quoted string where an enum is declared. The token
    still goes through the identifier check, so this opens no injection hole.
    """


def render_value(value: Any) -> str:
    """Render a Python value as a GraphQL literal."""
    if value is None:
        return "null"
    if isinstance(value, EnumValue):
        # Checked before the json.dumps fallthrough, which would quote it.
        return _identifier(value, "enum value")
    if isinstance(value, bool):
        # Checked before int, which bool subclasses, and before json.dumps,
        # which would spell these True/False.
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "[{}]".format(", ".join(render_value(item) for item in value))
    if isinstance(value, dict):
        members = ", ".join(
            f"{_identifier(k, 'input key')}: {render_value(v)}"
            for k, v in value.items()
        )
        return f"{{{members}}}"
    # GraphQL string, int and float literals share JSON's grammar, so
    # json.dumps is a complete and correct escaper for all three.
    return json.dumps(value)


def render_args(args: dict[str, Any]) -> str:
    """Render an argument list as `(key: value, ...)`; empty renders empty."""
    if not args:
        return ""
    rendered = ", ".join(
        f"{_identifier(key, 'argument')}: {render_value(value)}"
        for key, value in args.items()
    )
    return f"({rendered})"


def render_block(fields: dict[str, Any], depth: int) -> list[str]:
    """Render a selection dict to lines.

    A None value selects a bare field; a dict opens a nested block.
    """
    pad = INDENT * depth
    lines: list[str] = []
    for name, nested in fields.items():
        _identifier(name, "field")
        if isinstance(nested, dict):
            lines.append(f"{pad}{name} {{")
            lines.extend(render_block(nested, depth + 1))
            lines.append(f"{pad}}}")
        else:
            lines.append(f"{pad}{name}")
    return lines


def render_query(
    fields: dict[str, Any], *, kind: str, filters: dict[str, Any] | None = None
) -> str:
    """Render `query { <kind>(<filters>) { <fields> } }`."""
    _identifier(kind, "kind")
    lines = ["query {", f"{INDENT}{kind}{render_args(filters or {})} {{"]
    lines.extend(render_block(fields, 2))
    lines.extend([f"{INDENT}}}", "}"])
    return "\n".join(lines)


def render_mutation(
    fields: dict[str, Any],
    *,
    name: str,
    data: dict[str, Any],
    extra_args: dict[str, Any] | None = None,
) -> str:
    """Render `mutation { <name>(data: {...}) { <fields> } }`.

    Args:
        fields: The mutation payload's selection, e.g. {"ok": None}.
        name: The mutation name, e.g. "InfraDeviceUpdate".
        data: The `data` input object.
        extra_args: Arguments that sit beside `data` rather than inside it,
            which is where Infrahub puts `wait_until_completion`.
    """
    _identifier(name, "mutation")
    args: dict[str, Any] = {"data": data}
    args.update(extra_args or {})
    lines = ["mutation {", f"{INDENT}{name}{render_args(args)} {{"]
    lines.extend(render_block(fields, 2))
    lines.extend([f"{INDENT}}}", "}"])
    return "\n".join(lines)


class GraphQLRecord:
    """The result of a raw GraphQL query.

    Attributes:
        json: The full response body, with `data` and possibly `errors`.
        status_code: The HTTP status of the response.
    """

    def __init__(self, json: dict[str, Any], status_code: int) -> None:
        self.json = json
        self.status_code = status_code

    @property
    def data(self) -> Any:
        """The `data` member of the response, or None if absent."""
        return self.json.get("data")

    @property
    def errors(self) -> list[Any]:
        """Errors returned alongside a 200 response.

        Infrahub answers execution failures with 200 plus partial data and
        these errors; on this raw path they do not raise, so check them
        when a field comes back unexpectedly null.
        """
        return self.json.get("errors") or []

    def __repr__(self) -> str:
        return f"GraphQLRecord(status_code={self.status_code})"

    def __str__(self) -> str:
        return str(self.json)


class GraphQLQuery:
    """ih.graphql: raw GraphQL, plus stored queries.

    query() and execute() POST to /graphql[/{branch}]; stored() runs a
    saved CoreGraphQLQuery over the REST /api/query/{id} route, which is
    the one place Infrahub executes GraphQL outside /graphql.
    """

    def __init__(self, api: Api) -> None:
        self.api = api

    def _url(self, branch: str | None = None, at: str | None = None) -> str:
        """The endpoint url for a branch and timestamp.

        Infrahub reads the branch from the URL path (there is no `branch`
        query parameter on /graphql, unlike the REST routes) and the
        timestamp from `?at=`.
        """
        branch = branch if branch is not None else self.api.branch
        url = f"{self.api.base_url}/graphql"
        if branch:
            url = f"{url}/{segment(branch)}"
        if at:
            url = "{}?{}".format(url, urlencode({"at": at}))
        return url

    async def query(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        branch: str | None = None,
        at: str | None = None,
    ) -> GraphQLRecord:
        """Run a hand-written GraphQL query.

        Args:
            query: The query string.
            variables: Values for the query's declared variables.
            branch: Branch to run against, overriding the client default.
            at: Timestamp to read the graph as of.

        Returns:
            A GraphQLRecord wrapping the response body. A 200 carrying
            `errors` does not raise here: partial data is often still
            useful, and `.errors` exposes the rest.

        Raises:
            TypeError: If query is not a str or variables is not a dict.
                Checked up front because the server-side error for these
                is unhelpful.
            GraphQLError: If Infrahub rejects the query outright (HTTP
                400), carrying the parsed `errors` array.
        """
        if not isinstance(query, str):
            raise TypeError(f"query must be a str, got {type(query).__name__}")
        if variables is not None and not isinstance(variables, dict):
            raise TypeError(f"variables must be a dict, got {type(variables).__name__}")
        payload = {"query": query, "variables": variables}
        try:
            # A query is a read however it is spelled, so it retries.
            resp = await self.api._request_response(
                "POST", self._url(branch, at), json=payload, idempotent=True
            )
        except RequestError as e:
            # An unparseable query gets 400 and an `errors` array; anything
            # else is a plain transport/auth failure.
            if e.status_code == 400:
                try:
                    errors = e.response.json().get("errors")
                except ValueError:
                    errors = None
                if errors is not None:
                    raise GraphQLError(e.response, errors) from None
            raise
        return GraphQLRecord(self.api._decode(resp), resp.status_code)

    async def stored(
        self,
        query_id: str,
        *,
        variables: dict[str, Any] | None = None,
        branch: str | None = None,
        at: str | None = None,
        update_group: bool | None = None,
        subscribers: list[str] | None = None,
    ) -> GraphQLRecord:
        """Run a query stored on the server, by its id or its name.

        This is the one GraphQL call that is not a POST to /graphql:
        a CoreGraphQLQuery runs through /api/query/{id}, which is a REST
        route and therefore takes the branch as a query parameter rather
        than as a path suffix.

        Args:
            query_id: The stored query's id, or its name.
            variables: Values for the query's declared variables.
            branch: Branch to run against, overriding the client default.
            at: Timestamp to read the graph as of.
            update_group: Have the server maintain a group holding the
                nodes the query returned.
            subscribers: Node ids to add to that group as subscribers.
                The server ignores them unless update_group is true.

        Returns:
            A GraphQLRecord wrapping the response body, on the same terms
            as query(): a 200 carrying `errors` does not raise here.
        """
        params: dict[str, Any] = {}
        resolved = branch if branch is not None else self.api.branch
        if resolved:
            params["branch"] = resolved
        if at:
            params["at"] = at
        if update_group is not None:
            params["update_group"] = update_group
        if subscribers:
            # Repeatable: one `subscribers=` per id, which is how httpx
            # renders a list-valued parameter.
            params["subscribers"] = subscribers
        resp = await self.api._request_response(
            "POST",
            f"{self.api.base_url}/api/query/{segment(query_id)}",
            params=params,
            json={"variables": variables or {}},
            # A stored query is a read whatever the method says, exactly
            # as on the /graphql route.
            idempotent=True,
        )
        return GraphQLRecord(self.api._decode(resp), resp.status_code)

    async def execute(
        self,
        query: str,
        *,
        branch: str | None = None,
        at: str | None = None,
        idempotent: bool = False,
    ) -> dict[str, Any]:
        """Run a generated operation and return its `data`.

        This is the path the kind and branch layers use. Unlike query(),
        any `errors` in the body raise: get()/all() and the mutations have
        no partial-data story.

        Raises:
            GraphQLError: If the response body carries errors, including
                on the HTTP 200 Infrahub answers execution failures with.
        """
        resp = await self.api._request_response(
            "POST", self._url(branch, at), json={"query": query}, idempotent=idempotent
        )
        body = self.api._decode(resp)
        errors = body.get("errors")
        if errors:
            raise GraphQLError(resp, errors)
        return body.get("data") or {}
