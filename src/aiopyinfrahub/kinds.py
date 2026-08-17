"""KindEndpoint: actions available on one Infrahub schema kind."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from aiopyinfrahub.graphql import render_mutation
from aiopyinfrahub.models import KIND_MODELS
from aiopyinfrahub.response import Record, RecordSet
from aiopyinfrahub.schema import build_input, build_selection, lookup

if TYPE_CHECKING:
    from aiopyinfrahub.api import Api

# A positional pk is either a UUID (queried as `ids`) or a value for the
# kind's default_filter, and the two are told apart by shape.
UUID = re.compile(r"\A[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\Z")


class KindEndpoint:
    """One Infrahub schema kind, e.g. ih.InfraDevice.

    Attribute access on the Api builds these without validating the name
    and without any I/O; the first awaited operation fetches the branch
    schema and raises ValueError if the kind is not in it.
    """

    def __init__(self, api: Api, name: str) -> None:
        self.api = api
        self.name = name
        self.record_class = KIND_MODELS.get(name, Record)

    async def _node_schema(self, branch: str | None = None) -> dict[str, Any]:
        return lookup(await self.api.schema(branch=branch), self.name)

    async def get(
        self,
        pk: str | None = None,
        /,
        *,
        branch: str | None = None,
        at: str | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        properties: bool = False,
        **filters: Any,
    ) -> Record | None:
        """Get a single Record by primary key or by filters.

        Args:
            pk: A node UUID, or a value for the kind's `default_filter`
                (e.g. a device name). Which one is decided by shape.
            branch: Branch to read, overriding the client default.
            at: Timestamp to read the graph as of.
            include: Relationship names to add to the field selection.
            exclude: Attribute or relationship names to leave out.
            properties: Also fetch attribute and relationship metadata,
                reachable through `record.meta(name)`. Reads stay
                flattened either way.
            **filters: Infrahub filters instead of a pk, e.g.
                `name__value="sw-1"` or `hfid=["sw-1"]`; must match at
                most one object.

        Returns:
            The Record, or None if nothing matches.

        Raises:
            ValueError: If the filters match more than one object, or a
                non-UUID pk is given for a kind whose schema declares no
                default_filter.
        """
        if pk is not None:
            if UUID.match(pk):
                filters = {"ids": [pk]}
            else:
                node_schema = await self._node_schema(branch)
                default_filter = node_schema.get("default_filter")
                if not default_filter:
                    raise ValueError(
                        f"{self.name} declares no default_filter in its schema, "
                        f"so get({pk!r}) cannot be resolved; pass an explicit "
                        "filter such as name__value= or hfid=."
                    )
                filters = {default_filter: pk}
        it = aiter(
            self.filter(
                branch=branch,
                at=at,
                include=include,
                exclude=exclude,
                properties=properties,
                **filters,
            )
        )
        try:
            first = await anext(it, None)
            if first is None:
                return None
            if await anext(it, None) is not None:
                raise ValueError(
                    "get() returned more than one result. Check that the "
                    "filter(s) passed are valid for this kind or use filter() "
                    "or all() instead."
                )
        finally:
            await it.aclose()
        return first

    def filter(
        self,
        *,
        branch: str | None = None,
        at: str | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        properties: bool = False,
        **filters: Any,
    ) -> RecordSet:
        """Query the kind with filters; returns a lazy RecordSet.

        Args:
            branch: Branch to read, overriding the client default.
            at: Timestamp to read the graph as of.
            include: Relationship names to add to the field selection.
            exclude: Attribute or relationship names to leave out.
            properties: Also fetch attribute and relationship metadata,
                reachable through `record.meta(name)`.
            **filters: Infrahub filters. Attribute filters are suffixed
                (`name__value=`, `name__values=[...]`), relationships
                traverse (`site__name__value=`), and `ids=`, `hfid=` and
                `partial_match=` apply to every kind.

        Raises:
            ValueError: If called with no filters; use all() instead.
        """
        if not filters:
            raise ValueError("filter must be passed filters. Use all() instead.")
        return RecordSet(
            self,
            filters,
            branch=branch,
            at=at,
            include=include,
            exclude=exclude,
            properties=properties,
        )

    def all(
        self,
        limit: int = 0,
        offset: int | None = None,
        *,
        branch: str | None = None,
        at: str | None = None,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        properties: bool = False,
    ) -> RecordSet:
        """Return a RecordSet over every object of this kind.

        Args:
            limit: Page size for the query; 0 uses Api(page_size=).
            offset: Fetch only the single page starting here (requires
                limit) instead of iterating everything.
            branch: Branch to read, overriding the client default.
            at: Timestamp to read the graph as of.
            include: Relationship names to add to the field selection.
            exclude: Attribute or relationship names to leave out.
            properties: Also fetch attribute and relationship metadata,
                reachable through `record.meta(name)`.

        Raises:
            ValueError: If offset is given without a limit.
        """
        if offset is not None and not limit:
            raise ValueError("offset requires a positive limit value")
        return RecordSet(
            self,
            limit=limit,
            offset=offset,
            branch=branch,
            at=at,
            include=include,
            exclude=exclude,
            properties=properties,
        )

    async def count(
        self, *, branch: str | None = None, at: str | None = None, **filters: Any
    ) -> int:
        """Object count for the given filters (all objects if none)."""
        return await RecordSet(self, filters, branch=branch, at=at).count()

    async def create(
        self,
        fields: dict[str, Any] | None = None,
        /,
        *,
        branch: str | None = None,
        **kwargs: Any,
    ) -> Record:
        """Create an object with a <Kind>Create mutation.

        Args:
            fields: The field values as a dict, for callers holding them
                in one; otherwise pass them as keywords.
            branch: Branch to write to, overriding the client default.
                Mutations take no `at`: the server resets it to now.
            **kwargs: Field values. Scalars are wrapped per the schema
                (`{"value": v}` for attributes, `{"id": v}` for
                relationships); the full wire shape passes through.
        """
        return await self._mutate(
            "Create", fields if fields is not None else kwargs, branch
        )

    async def upsert(
        self,
        fields: dict[str, Any] | None = None,
        /,
        *,
        branch: str | None = None,
        **kwargs: Any,
    ) -> Record:
        """Create or update an object with a <Kind>Upsert mutation.

        The values must carry enough to resolve the kind's
        human_friendly_id or uniqueness constraints, or an id. See
        create() for how the values are shaped.
        """
        return await self._mutate(
            "Upsert", fields if fields is not None else kwargs, branch
        )

    async def _mutate(
        self, action: str, fields: dict[str, Any], branch: str | None
    ) -> Record:
        node_schema = await self._node_schema(branch)
        name = f"{self.name}{action}"
        query = render_mutation(
            {"ok": None, "object": build_selection(node_schema)},
            name=name,
            data=build_input(node_schema, fields),
        )
        data = await self.api.graphql.execute(query, branch=branch)
        return self.record_class(
            (data.get(name) or {}).get("object") or {},
            self.api,
            full=True,
            kind=self.name,
            branch=branch if branch is not None else self.api.branch,
        )
