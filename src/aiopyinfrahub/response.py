"""Record and RecordSet: objects returned by kind queries."""

from __future__ import annotations

import asyncio
import copy
import itertools
from collections import deque
from collections.abc import AsyncGenerator, Iterator
from difflib import get_close_matches
from typing import TYPE_CHECKING, Any

from aiopyinfrahub.graphql import render_mutation, render_query
from aiopyinfrahub.schema import build_input, build_selection, lookup, rel_selection

if TYPE_CHECKING:
    from aiopyinfrahub.api import Api
    from aiopyinfrahub.kinds import KindEndpoint


def _peer_inputs(peers: Any) -> list[dict[str, Any]]:
    """Normalize relationship peers to a list of RelatedNodeInput dicts.

    A str is a node id and a Record collapses to its own id; a dict is
    already the wire shape, which is how `hfid=` and `kind=` peers stay
    reachable. A bare peer is accepted as well as a list of them.
    """
    if isinstance(peers, (str, dict, Record)):
        peers = [peers]
    nodes: list[dict[str, Any]] = []
    for peer in peers:
        if isinstance(peer, Record):
            nodes.append({"id": peer.__dict__.get("id")})
        elif isinstance(peer, dict):
            nodes.append(peer)
        else:
            nodes.append({"id": peer})
    return nodes


def _serialize_value(v: Any) -> Any:
    if isinstance(v, Record):
        # Related peers collapse to their id, which is what RelatedNodeInput
        # wants back on the way out. __dict__ rather than getattr so a brief
        # peer without an id does not trip the full_details AttributeError.
        return v.__dict__.get("id")
    if isinstance(v, list):
        return [_serialize_value(i) for i in v]
    return v


class Record:
    """An Infrahub object parsed from a GraphQL response.

    Infrahub boxes every attribute as `{value: ...}` and every relationship
    as `{node: ...}` or `{count, edges}`. Those wrappers are flattened at
    parse, so `device.name` is the value and `device.site` is a brief
    Record, and the keys that were wrapped are remembered so a save()
    re-wraps them. A read passing `properties=True` keeps the rest of each
    wrapper as metadata, which `meta(name)` hands back and which is never
    serialized, diffed, or saved.

    Accessing a field that is absent (e.g. on a brief nested record) never
    triggers a request; it raises AttributeError and the caller must
    `await full_details()`.

    Records compare equal (and hash together) when they refer to the same
    object on the same branch: the same id on two branches is two states,
    and there is no url to key on. Records without an id fall back to
    identity comparison.
    """

    def __init__(
        self,
        values: dict[str, Any],
        api: Api,
        full: bool = False,
        *,
        kind: str | None = None,
        branch: str | None = None,
    ) -> None:
        self._has_details = full
        self._api = api
        # Nested peers carry no endpoint context, so __typename (which the
        # default selection always asks for) is what makes them saveable.
        self._kind = kind or values.get("__typename")
        self._branch = branch
        self._attr_keys: set[str] = set()
        self._rel_keys: set[str] = set()
        self._meta: dict[str, Any] = {}
        self._snapshot: dict[str, Any] = {}
        self._parse(values)
        self._snapshot = copy.deepcopy(self.serialize())

    def _metadata(self, values: dict[str, Any] | None) -> Record | None:
        """A metadata wrapper as a Record, or None when nothing was selected.

        `source` and `owner` stay plain dicts of the ids and labels the
        selection asked for: they name a lineage peer rather than being
        one, and hydrating them would invite a save() on metadata.
        """
        return Record(values, self._api, full=True) if values else None

    def _parse(self, values: dict[str, Any]) -> None:
        for k, v in values.items():
            if isinstance(v, dict) and "value" in v:
                # Attribute wrapper: {"value": ..., "is_protected": ...}.
                # The value flattens onto the record and the rest is
                # metadata, reachable only through meta().
                self._attr_keys.add(k)
                meta = self._metadata({m: n for m, n in v.items() if m != "value"})
                if meta is not None:
                    self._meta[k] = meta
                v = v["value"]
            elif isinstance(v, dict) and "node" in v:
                # Cardinality-one relationship; the node is null when unset.
                self._rel_keys.add(k)
                meta = self._metadata(v.get("properties"))
                if meta is not None:
                    self._meta[k] = meta
                node = v["node"]
                v = Record(node, self._api, branch=self._branch) if node else None
            elif isinstance(v, dict) and "edges" in v:
                # Cardinality-many relationship: {"count": n, "edges": [...]}.
                # Every edge carries its own properties, so the metadata is
                # a list positioned against the peers.
                self._rel_keys.add(k)
                edges = [edge for edge in v.get("edges") or [] if edge.get("node")]
                if any(edge.get("properties") for edge in edges):
                    self._meta[k] = [
                        self._metadata(edge.get("properties")) for edge in edges
                    ]
                v = [
                    Record(edge["node"], self._api, branch=self._branch)
                    for edge in edges
                ]
            try:
                setattr(self, k, v)
            except AttributeError as e:
                # A property on a subclass has no setter, and the raw error
                # names neither the field nor the class.
                raise AttributeError(
                    f"{type(self).__name__} cannot store the field {k!r}: it "
                    "collides with a property of the same name."
                ) from e

    def __getattr__(self, k: str) -> Any:
        if k.startswith("_"):
            raise AttributeError(k)
        if self._kind and not self._has_details:
            raise AttributeError(
                f"{k!r} is not loaded on this record. It may only be present on "
                "the full object; 'await record.full_details()' then retry."
            )
        raise AttributeError(f"Record has no attribute {k!r}")

    def _key(self) -> tuple[str | None, Any] | None:
        ident = self.__dict__.get("id")
        if ident is None:
            return None
        return (self._branch, ident)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Record):
            return NotImplemented
        key, other_key = self._key(), other._key()
        if key is None or other_key is None:
            return self is other
        return key == other_key

    def __hash__(self) -> int:
        key = self._key()
        return hash(key) if key is not None else id(self)

    def __iter__(self) -> Iterator[tuple[str, Any]]:
        for k, v in self.__dict__.items():
            if k.startswith("_"):
                continue
            if isinstance(v, Record):
                yield k, dict(v)
            elif isinstance(v, list):
                yield k, [dict(i) if isinstance(i, Record) else i for i in v]
            else:
                yield k, v

    def __getitem__(self, k: str) -> Any:
        return dict(self)[k]

    def __str__(self) -> str:
        # display_label is what the Infrahub UI shows and every node carries
        # it, so it wins over a name attribute the schema may not define.
        return getattr(self, "display_label", None) or getattr(self, "name", None) or ""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} ({self})>"

    def meta(self, name: str) -> Any:
        """Metadata for one attribute or relationship.

        Metadata is fetched only when the read passed `properties=True`,
        and it never leaves the record again: it is not serialized, not
        diffed, and not saved.

        Args:
            name: An attribute or relationship name on this record.

        Returns:
            For an attribute, a Record of its properties (is_protected,
            is_default, updated_at, source, owner). For a relationship,
            its properties: a Record for cardinality-one, or for
            cardinality-many a list positioned against the peer list,
            since Infrahub hangs the properties off each edge.

        Raises:
            ValueError: If the field carries no metadata, which is the
                case for every field of a record read without
                properties=True.
        """
        try:
            return self._meta[name]
        except KeyError:
            raise ValueError(
                f"No metadata for {name!r} on this record. Metadata is fetched "
                "only when the read passes properties=True."
            ) from None

    def serialize(self) -> dict[str, Any]:
        """Return a flat, JSON-able dict; peers collapse to ids.

        Only the keys parsed out of a relationship wrapper are collapsed:
        a JSON-kind attribute can hold dicts and lists of dicts, and those
        must survive untouched. `__typename` and the private bookkeeping
        attributes are skipped, since both start with an underscore.
        """
        return {
            k: _serialize_value(v) if k in self._rel_keys else v
            for k, v in self.__dict__.items()
            if not k.startswith("_")
        }

    def updates(self) -> dict[str, Any]:
        """Diff current state against the state at parse time."""
        current = self.serialize()
        init = self._snapshot
        return {k: v for k, v in current.items() if k not in init or v != init[k]}

    def _identity(self, verb: str) -> tuple[Any, str]:
        """The id and kind a mutation needs, or a ValueError explaining why
        this record cannot have one built for it."""
        ident, kind = self.__dict__.get("id"), self._kind
        if not ident or not kind:
            raise ValueError(
                f"Record has no id and __typename, and cannot be {verb}. Only "
                "records parsed from a node query carry both."
            )
        return ident, kind

    async def _node_schema(self, kind: str) -> dict[str, Any]:
        return lookup(await self._api.schema(branch=self._branch), kind)

    def _mutation_input(
        self, node_schema: dict[str, Any], updates: dict[str, Any]
    ) -> dict[str, Any]:
        """Re-wrap changed fields for an Update mutation's `data`.

        Keys unwrapped at parse are re-wrapped from that memory rather
        than by shape, because a JSON-kind attribute's value is itself a
        dict and build_input would take it for the wire form already.
        Fields the caller added that the record never parsed fall back to
        the schema.
        """
        wrapped = {
            key: {"value": value}
            for key, value in updates.items()
            if key in self._attr_keys
        }
        rest = {k: v for k, v in updates.items() if k not in self._attr_keys}
        return {**wrapped, **build_input(node_schema, rest)}

    async def full_details(self) -> bool:
        """Re-query this object with the kind's default field selection.

        Returns:
            True once details are loaded; False if the record carries no
            id and kind to query with, or the object is gone.
        """
        ident, kind = self.__dict__.get("id"), self._kind
        if not ident or not kind:
            return False
        node_schema = await self._node_schema(kind)
        # Every node query is paginated, even one pinned to a single id.
        query = render_query(
            {"edges": {"node": build_selection(node_schema)}},
            kind=kind,
            filters={"ids": [ident]},
        )
        data = await self._api.graphql.execute(
            query, branch=self._branch, idempotent=True
        )
        edges = (data.get(kind) or {}).get("edges") or []
        if not edges:
            return False
        self._parse(edges[0]["node"])
        self._has_details = True
        self._snapshot = copy.deepcopy(self.serialize())
        return True

    async def fetch(self, name: str) -> Record | list[Record] | None:
        """Hydrate one relationship onto this record and return it.

        Component and Generic many-relationships stay out of the default
        selection so list queries do not fan out across the graph, so this
        is how they are read: one query for this object with the
        relationship included. Nothing is prefetched implicitly, which is
        the whole point of spelling it as a call.

        Returns:
            The peer Record (None when unset) for a cardinality-one
            relationship, or the list of peers for a cardinality-many one.

        Raises:
            ValueError: If the record has no id and kind, or the kind's
                schema declares no such relationship.
        """
        ident, kind = self._identity("fetched from")
        node_schema = await self._node_schema(kind)
        relationships = {
            rel["name"]: rel for rel in node_schema.get("relationships") or []
        }
        rel = relationships.get(name)
        if rel is None:
            close = get_close_matches(name, relationships, n=3)
            hint = " Did you mean: {}?".format(", ".join(close)) if close else ""
            raise ValueError(f"{kind} has no relationship {name!r}.{hint}")
        query = render_query(
            {"edges": {"node": {"id": None, name: rel_selection(rel)}}},
            kind=kind,
            filters={"ids": [ident]},
        )
        data = await self._api.graphql.execute(
            query, branch=self._branch, idempotent=True
        )
        edges = (data.get(kind) or {}).get("edges") or []
        if not edges:
            # The object is gone from this branch; there is nothing to merge.
            return [] if rel.get("cardinality") == "many" else None
        self._parse({name: edges[0]["node"][name]})
        # Only this key is refreshed, so the rest of the snapshot (and any
        # edit still pending against it) survives the merge.
        self._snapshot[name] = _serialize_value(getattr(self, name))
        return getattr(self, name)

    async def add_related(self, name: str, peers: Any) -> bool:
        """Add peers to a relationship with the RelationshipAdd mutation.

        Args:
            name: The relationship's name in the kind's schema.
            peers: A node id, a Record, a RelatedNodeInput dict, or a list
                of any of those.

        Returns:
            The mutation's `ok` flag. RelationshipAdd answers with nothing
            else, so re-read or fetch() the relationship to see the result.

        Raises:
            ValueError: If the record has no id and kind.
        """
        return await self._relationship("RelationshipAdd", name, peers)

    async def remove_related(self, name: str, peers: Any) -> bool:
        """Remove peers from a relationship with RelationshipRemove.

        See add_related() for the accepted peer forms and the return value.

        Raises:
            ValueError: If the record has no id and kind.
        """
        return await self._relationship("RelationshipRemove", name, peers)

    async def _relationship(self, mutation: str, name: str, peers: Any) -> bool:
        ident, _ = self._identity("related to peers")
        query = render_mutation(
            {"ok": None},
            name=mutation,
            data={"id": ident, "name": name, "nodes": _peer_inputs(peers)},
        )
        data = await self._api.graphql.execute(query, branch=self._branch)
        return bool((data.get(mutation) or {}).get("ok"))

    async def save(self) -> bool:
        """Send changed fields as a <Kind>Update mutation.

        Only the diff is sent: Infrahub records every write on the branch,
        so a full-object update would manufacture diff noise for reviewers.

        Returns:
            True if a mutation was sent; False when nothing changed.

        Raises:
            ValueError: If the record has no id and kind.
        """
        updates = self.updates()
        # id is the mutation's key, not a field to set.
        updates.pop("id", None)
        if not updates:
            return False
        ident, kind = self._identity("saved")
        node_schema = await self._node_schema(kind)
        query = render_mutation(
            {"ok": None, "object": build_selection(node_schema)},
            name=f"{kind}Update",
            data={"id": ident, **self._mutation_input(node_schema, updates)},
        )
        data = await self._api.graphql.execute(query, branch=self._branch)
        self._parse((data.get(f"{kind}Update") or {}).get("object") or {})
        self._snapshot = copy.deepcopy(self.serialize())
        return True

    async def update(self, data: dict[str, Any]) -> bool:
        """Set fields from a dict and save(); see save() for semantics."""
        for k, v in data.items():
            setattr(self, k, v)
        return await self.save()

    async def delete(self) -> bool:
        """Delete the object with a <Kind>Delete mutation.

        Returns:
            The mutation's `ok` flag.

        Raises:
            ValueError: If the record has no id and kind.
        """
        ident, kind = self._identity("deleted")
        # <Kind>Delete answers with ok and no object.
        query = render_mutation({"ok": None}, name=f"{kind}Delete", data={"id": ident})
        data = await self._api.graphql.execute(query, branch=self._branch)
        return bool((data.get(f"{kind}Delete") or {}).get("ok"))


class RecordSet:
    """Lazy async iterable of Records for one kind.

    Nothing is fetched until iteration starts. After the first page, the
    remaining pages are fetched concurrently (bounded by Api.max_concurrency)
    and yielded in offset order. Each `async for` re-runs the query.
    """

    def __init__(
        self,
        endpoint: KindEndpoint,
        filters: dict[str, Any] | None = None,
        limit: int = 0,
        offset: int | None = None,
        *,
        branch: str | None = None,
        at: str | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        properties: bool = False,
    ) -> None:
        self.endpoint = endpoint
        self.filters = filters or {}
        self.limit = limit
        self.offset = offset
        self.branch = branch
        self.at = at
        self.include = include
        self.exclude = exclude
        self.properties = properties

    def __aiter__(self) -> AsyncGenerator[Record]:
        return self._iter()

    async def _iter(self) -> AsyncGenerator[Record]:
        endpoint = self.endpoint
        api = endpoint.api
        kind = endpoint.name
        record_class = endpoint.record_class
        node_schema = await endpoint._node_schema(self.branch)
        selection = build_selection(
            node_schema, self.include, self.exclude, self.properties
        )
        # The branch stamped onto every Record must be the resolved one, so
        # two Records off the same object on different branches differ.
        branch = self.branch if self.branch is not None else api.branch
        params = dict(self.filters)
        # offset/limit passed as filters behave exactly like the all() args.
        filter_offset = params.pop("offset", None)
        filter_limit = params.pop("limit", None)
        offset = self.offset if self.offset is not None else filter_offset
        page_size = self.limit or filter_limit or api.page_size

        async def fetch(page_offset: int) -> dict[str, Any]:
            query = render_query(
                {"count": None, "edges": {"node": selection}},
                kind=kind,
                filters={**params, "offset": page_offset, "limit": page_size},
            )
            data = await api.graphql.execute(
                query, branch=self.branch, at=self.at, idempotent=True
            )
            return data.get(kind) or {}

        page = await fetch(offset or 0)
        edges = page.get("edges") or []
        for edge in edges:
            yield record_class(edge["node"], api, full=True, kind=kind, branch=branch)
        # An explicit offset, whether from the constructor or the filters,
        # pins the query to that one page.
        if offset is not None or not edges:
            return
        # The server may cap `limit` below what was asked for, so the offset
        # arithmetic uses the page size it actually served; trusting the
        # requested size would skip the records between the two.
        page_size = len(edges)

        # Sliding window: at most max_concurrency page fetches are in flight,
        # so abandoning the iteration early neither fetches nor buffers the
        # rest of the pages.
        offsets = iter(range(page_size, page.get("count") or 0, page_size))
        window: deque[asyncio.Task[dict[str, Any]]] = deque(
            asyncio.create_task(fetch(page_offset))
            for page_offset in itertools.islice(offsets, api.max_concurrency)
        )
        try:
            while window:
                page = await window.popleft()
                next_offset = next(offsets, None)
                if next_offset is not None:
                    window.append(asyncio.create_task(fetch(next_offset)))
                for edge in page.get("edges") or []:
                    yield record_class(
                        edge["node"], api, full=True, kind=kind, branch=branch
                    )
        finally:
            for task in window:
                task.cancel()

    async def count(self) -> int:
        """Total object count for the query, in one limit=1 query."""
        endpoint = self.endpoint
        # Resolved even though count selects no fields: an unknown kind must
        # fail with the near-miss message rather than a server-side error.
        await endpoint._node_schema(self.branch)
        query = render_query(
            {"count": None},
            kind=endpoint.name,
            filters={**self.filters, "limit": 1},
        )
        data = await endpoint.api.graphql.execute(
            query, branch=self.branch, at=self.at, idempotent=True
        )
        return (data.get(endpoint.name) or {}).get("count") or 0
