"""Graph: shortest-path and reachability queries over the node graph."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiopyinfrahub.graphql import render_query

if TYPE_CHECKING:
    from aiopyinfrahub.api import Api

# PathNodeType: what every node in a traversal result looks like. It is
# not a node query, so there are no attribute wrappers here and no way to
# widen the selection; a caller who wants a node's own fields reads it
# through its kind afterwards.
NODE_FIELDS: dict[str, Any] = {
    "id": None,
    "kind": None,
    "label": None,
    "display_label": None,
    "hfid": None,
}

# PathResultType: the ordered hops, each naming the node it reached and
# the relationship it arrived on, which is null on the first hop because
# that hop is the source itself.
PATH_FIELDS: dict[str, Any] = {
    "depth": None,
    "hops": {
        "node": NODE_FIELDS,
        "relationship": {
            "kind": None,
            "from_rel": None,
            "to_rel": None,
            "from_label": None,
            "to_label": None,
        },
    },
}


class Graph:
    """ih.graph: walk the relationships between nodes.

    Server >= 1.10: InfrahubPathTraversal and InfrahubReachableNodes do
    not exist before it, and an older instance answers a call here with
    `Cannot query field ...` in the errors array, which surfaces as a
    GraphQLError.

    Results are plain data, not Records: a path is a description of the
    graph rather than an object to save, and each hop carries only enough
    of its node to identify it.
    """

    def __init__(self, api: Api) -> None:
        self.api = api

    async def _traverse(
        self, fields: dict[str, Any], data: dict[str, Any], branch: str | None
    ) -> dict[str, Any]:
        query = render_query(
            fields,
            kind="InfrahubPathTraversal",
            filters={"data": {k: v for k, v in data.items() if v is not None}},
        )
        result = await self.api.graphql.execute(query, branch=branch, idempotent=True)
        return result.get("InfrahubPathTraversal") or {}

    async def paths(
        self,
        source_id: str,
        target_id: str,
        *,
        max_depth: int | None = None,
        max_paths: int | None = None,
        shortest_paths_only: bool | None = None,
        kind_filter: list[str] | None = None,
        relationship_filter: list[str] | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        """Every path the server finds between two nodes.

        Args:
            source_id: UUID of the node to start from.
            target_id: UUID of the node to reach. The server calls this
                `destination_id`; the name here matches the sisters'.
            max_depth: Node hops to search through (server default 5,
                maximum 30).
            max_paths: Paths to return (server default 10, maximum 100).
            shortest_paths_only: True (the server's default) returns only
                the shortest path through each intermediate object;
                False returns every loopless path up to max_paths.
            kind_filter: Only traverse through nodes of these kinds.
            relationship_filter: Only follow relationships with these
                schema identifiers (`device__interface`), which are not
                the relationship names (`interfaces`).
            branch: Branch to traverse, overriding the client default.

        Returns:
            The PathTraversalResultType payload: `count`, the `source`
            and `destination` nodes, the `paths` themselves ordered
            shortest first, the concrete `excluded_kinds` the server
            applied, and `truncated_at_depth`, which is null when the
            search ran to completion and otherwise names the depth it ran
            out of budget at.
        """
        return await self._traverse(
            {
                "count": None,
                "truncated_at_depth": None,
                "excluded_kinds": None,
                "source": NODE_FIELDS,
                "destination": NODE_FIELDS,
                "paths": PATH_FIELDS,
            },
            {
                "source_id": source_id,
                "destination_id": target_id,
                "max_depth": max_depth,
                "max_paths": max_paths,
                "shortest_paths_only": shortest_paths_only,
                "kind_filter": kind_filter,
                "relationship_filter": relationship_filter,
            },
            branch,
        )

    async def path_exists(
        self,
        source_id: str,
        target_id: str,
        *,
        max_depth: int | None = None,
        kind_filter: list[str] | None = None,
        relationship_filter: list[str] | None = None,
        branch: str | None = None,
    ) -> bool:
        """Whether any path connects two nodes.

        The cheap form of paths(): one path is asked for and only the
        count is selected, so the server stops as soon as it has an
        answer. See paths() for the arguments.
        """
        result = await self._traverse(
            {"count": None},
            {
                "source_id": source_id,
                "destination_id": target_id,
                "max_depth": max_depth,
                "max_paths": 1,
                "kind_filter": kind_filter,
                "relationship_filter": relationship_filter,
            },
            branch,
        )
        return bool(result.get("count"))

    async def reachable_nodes(
        self,
        source_id: str,
        target_kinds: list[str],
        *,
        max_depth: int | None = None,
        max_paths: int | None = None,
        max_results: int | None = None,
        shortest_paths_only: bool | None = None,
        branch: str | None = None,
    ) -> dict[str, Any]:
        """Nodes of given kinds reachable from one node.

        Args:
            source_id: UUID of the node to start from.
            target_kinds: The kinds to look for. Required by the server.
            max_depth: Traversal depth (server default 5, maximum 30).
            max_paths: Total paths returned across every terminal found
                (server default 500, maximum 5000).
            max_results: Distinct terminal nodes to find (server default
                50, maximum 200).
            shortest_paths_only: True (the server's default) returns only
                the shortest path to each target; False returns every
                path within max_depth that matches.
            branch: Branch to traverse, overriding the client default.

        Returns:
            The ReachableNodesResultType payload: `count`, the `source`
            node, and one `dependencies` entry per (node, path) pair,
            each with its `depth`, the reached `node`, and the `path`
            taken to it.
        """
        data = {
            "source_id": source_id,
            "target_kinds": target_kinds,
            "max_depth": max_depth,
            "max_paths": max_paths,
            "max_results": max_results,
            "shortest_paths_only": shortest_paths_only,
        }
        query = render_query(
            {
                "count": None,
                "source": NODE_FIELDS,
                "dependencies": {
                    "depth": None,
                    "node": NODE_FIELDS,
                    "path": PATH_FIELDS,
                },
            },
            kind="InfrahubReachableNodes",
            filters={"data": {k: v for k, v in data.items() if v is not None}},
        )
        result = await self.api.graphql.execute(query, branch=branch, idempotent=True)
        return result.get("InfrahubReachableNodes") or {}
