"""Pools: allocation from Infrahub's resource pools, and reports on them."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiopyinfrahub.graphql import render_mutation, render_query
from aiopyinfrahub.response import Record

if TYPE_CHECKING:
    from aiopyinfrahub.api import Api

# PoolAllocatedNode, verified against the 1.10 SDL. It is the payload of
# both GetResource mutations and of the allocation report, and it
# describes an allocation rather than the node itself: these five fields
# are all it has, which is why the Records it yields are brief.
ALLOCATED_FIELDS: dict[str, Any] = {
    "id": None,
    "kind": None,
    "identifier": None,
    "display_label": None,
    "branch": None,
}

# PoolUtilization plus the IPPoolUtilizationResource hung off each edge.
UTILIZATION_FIELDS: dict[str, Any] = {
    "count": None,
    "utilization": None,
    "utilization_branches": None,
    "utilization_default_branch": None,
    "edges": {
        "node": {
            "id": None,
            "kind": None,
            "display_label": None,
            "utilization": None,
            "utilization_branches": None,
            "utilization_default_branch": None,
            "weight": None,
        }
    },
}


def _pool_id(value: Any) -> str:
    """The id of a pool or resource given as a Record or as a string."""
    # __dict__ rather than getattr, so a brief Record carrying no id
    # fails here rather than tripping the full_details AttributeError.
    ident = value.__dict__.get("id") if isinstance(value, Record) else value
    if not isinstance(ident, str) or not ident:
        raise ValueError(f"{value!r} is neither an id nor a Record carrying one.")
    return ident


class Pools:
    """ih.pools: allocate from resource pools and report on them.

    Allocation is a mutation, not a read: the pool hands out a resource
    and records it, so nothing here takes an `at`. The two reports return
    plain dicts and lists rather than Records, because a utilization
    figure is not an object anyone saves.
    """

    def __init__(self, api: Api) -> None:
        self.api = api

    async def _allocate(
        self,
        mutation: str,
        pool: Any,
        identifier: str | None,
        prefix_length: int | None,
        data: dict[str, Any] | None,
        branch: str | None,
    ) -> Record:
        payload: dict[str, Any] = {"id": _pool_id(pool)}
        if identifier is not None:
            payload["identifier"] = identifier
        if prefix_length is not None:
            payload["prefix_length"] = prefix_length
        if data is not None:
            payload["data"] = data
        query = render_mutation(
            {"ok": None, "node": ALLOCATED_FIELDS}, name=mutation, data=payload
        )
        result = await self.api.graphql.execute(query, branch=branch)
        node = (result.get(mutation) or {}).get("node") or {}
        # The payload names both the allocated node's kind and the branch
        # it landed on, so the Record is addressed correctly for a
        # full_details() even when the client has no default branch.
        return Record(node, self.api, kind=node.get("kind"), branch=node.get("branch"))

    async def next_ip_address(
        self,
        pool: Any,
        *,
        identifier: str | None = None,
        prefix_length: int | None = None,
        data: dict[str, Any] | None = None,
        branch: str | None = None,
    ) -> Record:
        """Allocate the next free address from an IP address pool.

        Args:
            pool: The pool to allocate from, as a Record or as an id.
            identifier: A key for the allocation. Asking twice with the
                same identifier returns the same address rather than
                consuming another, which is what makes the call safe to
                repeat from a generator.
            prefix_length: Mask length for the new address, overriding
                the pool's default.
            data: Extra fields for the node the pool creates, in the
                mutation's wire shape (e.g. `{"description": "..."}`).
            branch: Branch to allocate on, overriding the client default.

        Returns:
            The allocated node as a brief Record: the mutation answers
            with the allocation (id, kind, identifier, display_label,
            branch) and not with the node's own fields, so reading one of
            those raises AttributeError naming `await full_details()`.
        """
        return await self._allocate(
            "InfrahubIPAddressPoolGetResource",
            pool,
            identifier,
            prefix_length,
            data,
            branch,
        )

    async def next_ip_prefix(
        self,
        pool: Any,
        *,
        identifier: str | None = None,
        prefix_length: int | None = None,
        data: dict[str, Any] | None = None,
        branch: str | None = None,
    ) -> Record:
        """Allocate the next free prefix from an IP prefix pool.

        See next_ip_address() for the arguments and the return value;
        `prefix_length` sizes the new prefix here rather than masking a
        single address.
        """
        return await self._allocate(
            "InfrahubIPPrefixPoolGetResource",
            pool,
            identifier,
            prefix_length,
            data,
            branch,
        )

    async def utilization(self, pool: Any) -> dict[str, Any]:
        """How much of a pool is in use, overall and per resource.

        Args:
            pool: The pool, as a Record or as an id.

        Returns:
            The PoolUtilization payload: the pool's resource `count` and
            its three utilization percentages (overall, branches only,
            default branch only), plus one `edges` entry per resource
            with the same figures and the resource's relative `weight`.
        """
        query = render_query(
            UTILIZATION_FIELDS,
            kind="InfrahubResourcePoolUtilization",
            filters={"pool_id": _pool_id(pool)},
        )
        data = await self.api.graphql.execute(query, idempotent=True)
        return data.get("InfrahubResourcePoolUtilization") or {}

    async def allocated(
        self,
        pool: Any,
        resource: Any,
        *,
        offset: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """What a pool has handed out from one of its resources.

        Args:
            pool: The pool, as a Record or as an id.
            resource: The resource inside it that was allocated from (a
                prefix, for an IP pool), as a Record or as an id. The
                query declares `resource_id: String!`, so this is
                required rather than optional as the pool-wide reading of
                the name would suggest.
            offset: Where in the allocation list to start.
            limit: How many allocations to return.

        Returns:
            One plain dict per allocation, each carrying the allocated
            node's id, kind, identifier, display_label and branch.
        """
        filters: dict[str, Any] = {
            "pool_id": _pool_id(pool),
            "resource_id": _pool_id(resource),
        }
        if offset is not None:
            filters["offset"] = offset
        if limit is not None:
            filters["limit"] = limit
        query = render_query(
            {"count": None, "edges": {"node": ALLOCATED_FIELDS}},
            kind="InfrahubResourcePoolAllocated",
            filters=filters,
        )
        data = await self.api.graphql.execute(query, idempotent=True)
        page = data.get("InfrahubResourcePoolAllocated") or {}
        return [edge["node"] for edge in page.get("edges") or []]
