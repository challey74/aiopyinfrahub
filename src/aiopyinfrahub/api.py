"""Api: the entry point to aiopyinfrahub."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncGenerator
from importlib.metadata import version as _version
from types import TracebackType
from typing import Any, Self

import httpx2

from aiopyinfrahub.artifacts import Artifacts
from aiopyinfrahub.branches import Branches
from aiopyinfrahub.diff import Diff
from aiopyinfrahub.exceptions import ContentError, ConvergenceTimeoutError, RequestError
from aiopyinfrahub.graph import Graph
from aiopyinfrahub.graphql import GraphQLQuery, render_mutation, render_query
from aiopyinfrahub.kinds import KindEndpoint
from aiopyinfrahub.kinds_generated import KindHints
from aiopyinfrahub.pools import Pools
from aiopyinfrahub.response import Record
from aiopyinfrahub.schema import flatten
from aiopyinfrahub.storage import Storage
from aiopyinfrahub.tasks import Tasks
from aiopyinfrahub.transforms import Transforms

# Read once at import: importlib.metadata.version() reads package metadata
# from disk, which is far too slow to repeat per request.
USER_AGENT = f"python-aiopyinfrahub/{_version('aiopyinfrahub')}"


# KindHints is generated: one annotation per kind on a demo instance, so
# editors complete `ih.InfraDevice`. It creates no runtime attributes and
# is not a whitelist; __getattr__ below is what builds every KindEndpoint.
class Api(KindHints):
    """Async Infrahub API client.

    Use as an async context manager so the connection pool is closed:

        async with aiopyinfrahub.api("https://infrahub", token="...") as ih:
            device = await ih.InfraDevice.get(name__value="sw-1")

    Any non-underscore attribute is a schema kind (`ih.InfraDevice`), built
    without I/O; the first awaited operation on it fetches and caches the
    branch schema.

    Every request-making method raises RequestError on a non-success
    response and ContentError when a successful response isn't JSON;
    transient failures are retried per `retries` before surfacing. Every
    operation built on generated GraphQL (kinds, branches, and the
    managers) additionally raises GraphQLError when the response body
    carries an `errors` array, which Infrahub sends with HTTP 200.
    Per-method Raises sections elsewhere list only conditions beyond these.

    Args:
        url: Base Infrahub URL. No suffix is appended: GraphQL lives at
            /graphql and REST under /api, so both are built from the root.
        token: API token, sent as `X-INFRAHUB-KEY`. None omits the header,
            which Infrahub allows for reads but not for mutations.
        username: Account name for JWT auth, paired with `password` and
            mutually exclusive with `token`. The first request that needs
            credentials logs in; `await ih.login()` does it eagerly.
        password: Password for `username`. Kept private and never logged.
        branch: Default branch for every operation. None uses the server's
            default branch (normally "main").
        timeout: Per-request timeout in seconds. Ignored when `client` is
            supplied; set it on that client instead.
        max_concurrency: Concurrent page fetches per result-set iteration.
        retries: Bound on automatic retries with exponential backoff and
            jitter. 429 is retried for anything (honoring Retry-After);
            transient 502/503/504 and connection failures retry only for
            idempotent operations, since an ambiguous write may have been
            processed. 0 disables.
        page_size: Default GraphQL page size, matching Infrahub's own.
        client: Custom httpx2.AsyncClient (SSL config, proxies, mock
            transports). A supplied client is yours to close; the Api
            closes only clients it creates itself.
    """

    def __init__(
        self,
        url: str,
        token: str | None = None,
        *,
        username: str | None = None,
        password: str | None = None,
        branch: str | None = None,
        timeout: float = 30.0,
        max_concurrency: int = 4,
        retries: int = 3,
        page_size: int = 50,
        client: httpx2.AsyncClient | None = None,
    ) -> None:
        # The server resolves an API key before a JWT, so sending both
        # would silently ignore the credentials the caller cared about.
        if token and (username or password):
            raise ValueError(
                "token= and username=/password= are mutually exclusive; "
                "Infrahub answers an API key and a JWT with different "
                "sessions and resolves the key first."
            )
        # The bare root, unlike the sister libraries: Infrahub serves
        # GraphQL at /graphql and REST at /api/..., so neither prefix can
        # be baked in here.
        self.base_url = url.rstrip("/")
        self.token = token
        self.username = username
        self._password = password
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        # Serializes login and refresh, so concurrent first requests log
        # in once rather than racing for a session each.
        self._auth_lock = asyncio.Lock()
        self.branch = branch
        self.max_concurrency = max_concurrency
        self.retries = retries
        self.page_size = page_size
        # Keyed by the resolved branch, which is None when the client has
        # no default and the caller named none: that is a distinct cache
        # entry from any named branch, even the server's default.
        self._schema_cache: dict[str | None, dict[str, dict[str, Any]]] = {}
        self._schema_lock = asyncio.Lock()

        # follow_redirects because an instance behind a proxy may redirect
        # http to https, and every operation here is a single fixed URL.
        self._owns_client = client is None
        self._client = (
            client
            if client is not None
            else httpx2.AsyncClient(timeout=timeout, follow_redirects=True)
        )

        self.branches = Branches(self)
        self.graphql = GraphQLQuery(self)
        self.tasks = Tasks(self)
        self.pools = Pools(self)
        self.diff = Diff(self)
        self.graph = Graph(self)
        self.storage = Storage(self)
        self.artifacts = Artifacts(self)
        self.transforms = Transforms(self)

    def __getattr__(self, name: str) -> KindEndpoint:
        if name.startswith("_"):
            raise AttributeError(name)
        return KindEndpoint(self, name)

    def kind(self, name: str) -> KindEndpoint:
        """A KindEndpoint for a kind held in a string, e.g.
        ih.kind("InfraDevice")."""
        return KindEndpoint(self, name)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the connection pool, if this Api created it.

        A client passed in via `client=` is the caller's to close
        (httpx2 convention), so sharing one client across Api instances
        is safe.
        """
        if self._owns_client:
            await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
        if self.token:
            # Infrahub's API-token header, which the server resolves before
            # any JWT; the constructor rejects holding both.
            headers["X-INFRAHUB-KEY"] = self.token
        elif self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"
        return headers

    async def _login(self) -> None:
        """POST /api/auth/login and adopt the pair of JWTs it answers with."""
        data = self._decode(
            await self._send(
                "POST",
                f"{self.base_url}/api/auth/login",
                json={"username": self.username, "password": self._password},
                idempotent=False,
            )
        )
        self._access_token = data.get("access_token")
        self._refresh_token = data.get("refresh_token")

    async def _authenticate(self) -> None:
        """Log in unless another task already did."""
        async with self._auth_lock:
            if self._access_token is None:
                await self._login()

    async def _reauthenticate(self, stale: str | None) -> None:
        """Replace an access token the server rejected.

        Args:
            stale: The access token that drew the 401. Another task may
                have replaced it already while this one waited on the
                lock, in which case retrying is all that is left to do.
        """
        async with self._auth_lock:
            if self._access_token != stale:
                return
            try:
                data = self._decode(
                    await self._send(
                        "POST",
                        f"{self.base_url}/api/auth/refresh",
                        # The refresh token travels in the Authorization
                        # header; the route takes no body at all.
                        headers={"Authorization": f"Bearer {self._refresh_token}"},
                        idempotent=False,
                    )
                )
            except RequestError as e:
                # An expired refresh token 401s in turn. The credentials
                # are still good, so start a new session rather than
                # surfacing an auth failure the caller cannot act on.
                if e.status_code != 401:
                    raise
                self._access_token = self._refresh_token = None
                await self._login()
            else:
                self._access_token = data.get("access_token")

    async def login(self) -> None:
        """Log in now instead of on the first request that needs it.

        A no-op when a session is already held, so it is also how a client
        re-authenticates after logout().

        Raises:
            ValueError: If the client was built without username/password.
        """
        if self._password is None:
            raise ValueError(
                "login() needs credentials; build the client with "
                "api(url, username=..., password=...)."
            )
        await self._authenticate()

    async def logout(self) -> None:
        """End the JWT session and drop both tokens.

        A no-op when no JWTs are held: an API token is not a session and
        has nothing to end.
        """
        if self._access_token is None:
            return
        try:
            await self._send(
                "POST", f"{self.base_url}/api/auth/logout", idempotent=False
            )
        finally:
            # Dropped even if the call failed: this client is done
            # presenting these tokens either way.
            self._access_token = self._refresh_token = None

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        """Delay in seconds before retry `attempt` (0-based)."""
        if retry_after is not None:
            try:
                # Honor Retry-After, capped so a broken proxy can't stall us.
                return min(float(retry_after), 60.0)
            except ValueError:
                pass  # HTTP-date form; fall through to exponential backoff
        delay = min(0.5 * 2**attempt, 8.0)
        return delay * (0.5 + random.random() / 2)

    async def _request_response(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        files: Any = None,
        headers: dict[str, str] | None = None,
        idempotent: bool | None = None,
    ) -> httpx2.Response:
        """Send one request, authenticating and retrying as needed.

        Keeping the JWT dance here rather than at the call sites is the
        same reasoning as the retry loop it wraps: both are transport
        plumbing, and neither is something a caller can act on.

        Args:
            files: Multipart parts, in httpx2's form, for the one upload
                route Infrahub has. Passed through rather than posted by
                the caller directly, so a multipart upload still gets the
                auth headers and the retry policy.
            idempotent: Whether repeating the request is safe. Defaults to
                `method == "GET"`, but every node read is a POST to
                /graphql, so the GraphQL layer sets this explicitly rather
                than letting the method decide.
        """
        if self._password is not None and self._access_token is None:
            # Lazy login: the first request needing credentials pays for it.
            await self._authenticate()
        stale = self._access_token
        try:
            return await self._send(
                method,
                url,
                params=params,
                json=json,
                files=files,
                headers=headers,
                idempotent=idempotent,
            )
        except RequestError as e:
            # Only a JWT session can recover from a 401; a rejected API
            # token stays rejected however often it is presented.
            if e.status_code != 401 or stale is None:
                raise
        await self._reauthenticate(stale)
        return await self._send(
            method,
            url,
            params=params,
            json=json,
            files=files,
            headers=headers,
            idempotent=idempotent,
        )

    async def _send(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        files: Any = None,
        headers: dict[str, str] | None = None,
        idempotent: bool | None = None,
    ) -> httpx2.Response:
        """Send one request, retrying transient failures.

        The auth routes call this directly: a 401 from a login or refresh
        is the answer, not something to re-authenticate and replay.
        """
        merged = {**self._headers(), **(headers or {})}
        if idempotent is None:
            idempotent = method == "GET"
        attempt = 0
        while True:
            retry_after = None
            try:
                resp = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    files=files,
                    headers=merged,
                )
            except httpx2.TransportError:
                # An ambiguous failure is only safely repeatable for reads:
                # a timed-out write may have been processed server-side.
                if not idempotent or attempt >= self.retries:
                    raise
            else:
                if resp.status_code == 429 and attempt < self.retries:
                    # Rejected without processing; safe to retry anything.
                    retry_after = resp.headers.get("Retry-After")
                elif (
                    resp.status_code in (502, 503, 504)
                    and idempotent
                    and attempt < self.retries
                ):
                    pass
                else:
                    if not resp.is_success:
                        raise RequestError(resp)
                    return resp
            await asyncio.sleep(self._backoff(attempt, retry_after))
            attempt += 1

    @staticmethod
    def _decode(resp: httpx2.Response) -> Any:
        try:
            return resp.json()
        except ValueError:
            raise ContentError(resp) from None

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
        headers: dict[str, str] | None = None,
        idempotent: bool | None = None,
    ) -> Any:
        resp = await self._request_response(
            method,
            url,
            params=params,
            json=json,
            headers=headers,
            idempotent=idempotent,
        )
        if method == "DELETE":
            return True
        return self._decode(resp)

    async def search(
        self,
        q: str,
        *,
        limit: int | None = None,
        partial_match: bool | None = None,
        case_sensitive: bool | None = None,
        branch: str | None = None,
    ) -> AsyncGenerator[Record]:
        """Search every kind for a string, lazily.

        InfrahubSearchAnywhere answers with `id` and `kind` per hit and
        nothing else, so the Records are brief: reading any other field
        raises AttributeError naming `await record.full_details()`, which
        re-queries the hit against its own kind. The query takes a `limit`
        but no `offset`, so this is one request, not a paginated set;
        calling it again re-runs it.

        Args:
            q: The string to look for.
            limit: Maximum number of hits the server returns.
            partial_match: Match substrings rather than whole values.
            case_sensitive: Match case exactly.
            branch: Branch to search, overriding the client default.
        """
        args = {
            "q": q,
            "limit": limit,
            "partial_match": partial_match,
            "case_sensitive": case_sensitive,
        }
        query = render_query(
            {"count": None, "edges": {"node": {"id": None, "kind": None}}},
            kind="InfrahubSearchAnywhere",
            filters={k: v for k, v in args.items() if v is not None},
        )
        data = await self.graphql.execute(query, branch=branch, idempotent=True)
        resolved = branch if branch is not None else self.branch
        for edge in (data.get("InfrahubSearchAnywhere") or {}).get("edges") or []:
            node = edge["node"]
            # The hit names its kind in `kind`, not __typename, so it is
            # passed explicitly; that is what full_details() re-queries.
            yield Record(node, self, kind=node.get("kind"), branch=resolved)

    async def convert_object_type(
        self,
        node_id: str,
        target_kind: str,
        fields_mapping: dict[str, Any],
        *,
        branch: str | None = None,
    ) -> Record | bool:
        """Convert a node to another kind with the ConvertObjectType mutation.

        Args:
            node_id: The node to convert.
            target_kind: The kind to convert it into.
            fields_mapping: How the target kind's fields are filled from
                the source node, e.g. `{"name": {"source_field": "name"}}`.
            branch: Branch to write to, overriding the client default.

        Returns:
            The converted node as a Record, or the mutation's `ok` flag
            when the payload carries no node. `node` is declared
            GenericScalar on the server, so its shape is whatever the
            mutation emits rather than a selection this client chose.
        """
        query = render_mutation(
            {"ok": None, "node": None},
            name="ConvertObjectType",
            data={
                "node_id": node_id,
                "target_kind": target_kind,
                "fields_mapping": fields_mapping,
            },
        )
        data = await self.graphql.execute(query, branch=branch)
        result = data.get("ConvertObjectType") or {}
        node = result.get("node")
        if not isinstance(node, dict):
            return bool(result.get("ok"))
        return Record(
            node,
            self,
            full=True,
            kind=target_kind,
            branch=branch if branch is not None else self.branch,
        )

    async def version(self) -> str:
        """The Infrahub server version, from GET /api/info."""
        data = await self._request("GET", f"{self.base_url}/api/info")
        return data.get("version", "")

    async def schema(
        self, branch: str | None = None, refresh: bool = False
    ) -> dict[str, dict[str, Any]]:
        """The branch's schema as a {kind: node_schema} map, cached.

        Nodes, generics, profiles and templates are flattened into one map
        keyed by GraphQL type name (namespace + name), which is the form
        every other module wants.

        Args:
            branch: Branch to read, overriding the client default.
            refresh: Refetch even if the branch is already cached. The
                schema is per-branch and mutable, so this is how a client
                picks up a schema load someone else performed.
        """
        key = branch if branch is not None else self.branch
        if not refresh and key in self._schema_cache:
            return self._schema_cache[key]
        # The lock keeps concurrent first calls to a single fetch.
        async with self._schema_lock:
            if refresh or key not in self._schema_cache:
                params = {"branch": key} if key else None
                data = await self._request(
                    "GET", f"{self.base_url}/api/schema", params=params
                )
                self._schema_cache[key] = flatten(data)
            return self._schema_cache[key]

    async def load_schemas(
        self, schemas: list[dict[str, Any]], *, branch: str | None = None
    ) -> dict[str, Any]:
        """Load schema documents into a branch, via POST /api/schema/load.

        Args:
            schemas: The documents, each the parsed form of one schema
                file (`{"version": "1.0", "nodes": [...]}`).
            branch: Branch to load into, overriding the client default.

        Returns:
            The SchemaUpdate payload: `hash`, `previous_hash`, `diff`,
            `warnings` and `schema_updated`. A successful load is not yet
            a converged instance, since the workers pick the new schema
            up asynchronously; wait_schemas_converged() waits for that.
        """
        key = branch if branch is not None else self.branch
        data = await self._request(
            "POST",
            f"{self.base_url}/api/schema/load",
            params={"branch": key} if key else None,
            json={"schemas": schemas},
            idempotent=False,
        )
        # That branch's cached schema is stale by definition now. It is
        # dropped rather than refetched, so the next read pays for a
        # request only if there is one.
        self._schema_cache.pop(key, None)
        return data

    async def check_schemas(
        self, schemas: list[dict[str, Any]], *, branch: str | None = None
    ) -> dict[str, Any]:
        """Ask what loading these schemas would do, changing nothing.

        Args:
            schemas: The documents, as for load_schemas().
            branch: Branch to check against, overriding the client
                default.

        Returns:
            The route's HTTP 202 body: `{"diff": ..., "warnings": [...]}`.
            Nothing is written, so no cache entry is dropped.
        """
        key = branch if branch is not None else self.branch
        return await self._request(
            "POST",
            f"{self.base_url}/api/schema/check",
            params={"branch": key} if key else None,
            json={"schemas": schemas},
            idempotent=False,
        )

    async def schema_in_sync(self) -> bool:
        """Whether every worker holds the current schema hash.

        InfrahubStatus is instance-wide rather than per-branch, so this
        takes no branch: it reports on the workers, not on a schema.
        """
        query = render_query(
            {"summary": {"schema_hash_synced": None}}, kind="InfrahubStatus"
        )
        data = await self.graphql.execute(query, idempotent=True)
        summary = (data.get("InfrahubStatus") or {}).get("summary") or {}
        return bool(summary.get("schema_hash_synced"))

    async def wait_schemas_converged(
        self,
        # ASYNC109 wants asyncio.timeout() around the call instead. As in
        # tasks.wait(), this is a poll loop rather than one cancellable
        # await, and the deadline belongs in the signature callers read.
        timeout: float = 60.0,  # noqa: ASYNC109
        interval: float = 1.0,
    ) -> None:
        """Poll InfrahubStatus until the schema hash is synced everywhere.

        A schema load returns as soon as the new schema is stored, and
        the workers adopt it on their own schedule, so this is what a
        caller waits on before reading or writing against it.

        Args:
            timeout: Seconds to poll before giving up.
            interval: Seconds between polls.

        Raises:
            ConvergenceTimeoutError: If the workers had still not
                converged when `timeout` elapsed.
        """
        deadline = time.monotonic() + timeout
        while True:
            if await self.schema_in_sync():
                return
            # Checked before sleeping, so the deadline is not overshot by
            # a whole interval before it is noticed.
            if time.monotonic() + interval > deadline:
                raise ConvergenceTimeoutError(timeout)
            await asyncio.sleep(interval)
