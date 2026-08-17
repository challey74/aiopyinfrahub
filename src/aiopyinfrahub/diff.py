"""Diff: what changed on a branch, as GraphQL trees and REST file lists."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiopyinfrahub.graphql import EnumValue, render_query

if TYPE_CHECKING:
    from aiopyinfrahub.api import Api

# DiffTreeSummary's whole field set (verified against the 1.10 SDL): it
# is nothing but the counts, which is the point of asking for it instead
# of the tree.
SUMMARY_FIELDS: dict[str, Any] = {
    "base_branch": None,
    "diff_branch": None,
    "from_time": None,
    "to_time": None,
    "num_added": None,
    "num_removed": None,
    "num_updated": None,
    "num_unchanged": None,
    "num_conflicts": None,
    "num_untracked_base_changes": None,
    "num_untracked_diff_changes": None,
}

# DiffTree carries `name` and `nodes` and has no num_unchanged. The node
# selection stops at each attribute's name and status: a DiffNode also
# carries its relationships, each of those its elements, and each element
# its properties, so selecting the whole tree fans out without bound.
# Callers who need the property values write the query themselves.
# `parent` stays in because it is three scalars and it is the only thing
# tree(include_parents=True) would otherwise fetch and throw away.
TREE_FIELDS: dict[str, Any] = {
    "base_branch": None,
    "diff_branch": None,
    "from_time": None,
    "to_time": None,
    "name": None,
    "num_added": None,
    "num_removed": None,
    "num_updated": None,
    "num_conflicts": None,
    "num_untracked_base_changes": None,
    "num_untracked_diff_changes": None,
    "nodes": {
        "uuid": None,
        "kind": None,
        "label": None,
        "status": None,
        "path_identifier": None,
        "contains_conflict": None,
        "num_added": None,
        "num_removed": None,
        "num_updated": None,
        "num_conflicts": None,
        "parent": {"uuid": None, "kind": None, "relationship_name": None},
        "attributes": {"name": None, "status": None, "contains_conflict": None},
    },
}


def _enums(filters: dict[str, Any]) -> dict[str, Any]:
    """Mark the DiffAction values in `status` as bare enum tokens.

    DiffTreeQueryFilters types `status` as IncExclFilterStatusOptions,
    whose includes/excludes are DiffAction enums; every other member of
    the input is a String, so nothing else needs marking.
    """
    status = filters.get("status")
    if not isinstance(status, dict):
        return filters
    marked = {key: [EnumValue(v) for v in values] for key, values in status.items()}
    return {**filters, "status": marked}


class Diff:
    """ih.diff: read what a branch changed against the branch it came from.

    A diff is a report rather than a set of objects, so everything here
    returns plain dicts and lists; Records are for nodes. The branch is a
    query argument, not a URL path suffix: DiffTree takes `branch:` and
    the two REST routes take `?branch=`, so nothing on this manager runs
    against a branch-scoped endpoint.
    """

    def __init__(self, api: Api) -> None:
        self.api = api

    @staticmethod
    def _args(
        branch: str,
        from_time: str | None,
        to_time: str | None,
        name: str | None,
        filters: dict[str, Any] | None,
        **extra: Any,
    ) -> dict[str, Any]:
        args: dict[str, Any] = {
            "branch": branch,
            "from_time": from_time,
            "to_time": to_time,
            "name": name,
            "filters": _enums(filters) if filters else None,
            **extra,
        }
        return {key: value for key, value in args.items() if value is not None}

    async def tree(
        self,
        branch: str,
        *,
        from_time: str | None = None,
        to_time: str | None = None,
        name: str | None = None,
        offset: int | None = None,
        limit: int | None = None,
        include_parents: bool | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """The diff of one branch, node by node.

        Args:
            branch: The branch to diff against the one it branched from.
            from_time: Start of the window, defaulting server-side to the
                branch point.
            to_time: End of the window, defaulting server-side to now.
            name: Read a named diff instead of the branch's own.
            offset: Where in the `nodes` list to start.
            limit: How many nodes to return.
            include_parents: Also return each changed node's parent, so
                the tree can be rendered hierarchically.
            filters: A DiffTreeQueryFilters input, e.g.
                `{"kind": {"includes": ["InfraDevice"]}}` or
                `{"status": {"includes": ["ADDED"]}}`; status values are
                DiffAction names and are rendered as enum tokens.

        Returns:
            The DiffTree payload, or None when the server holds no diff
            for that branch. Each node carries its own counts and its
            attributes' names and statuses; relationships and property
            values are deliberately left out (see TREE_FIELDS), so reach
            for `ih.graphql.query()` when a full tree is what you want.
        """
        args = self._args(
            branch,
            from_time,
            to_time,
            name,
            filters,
            offset=offset,
            limit=limit,
            include_parents=include_parents,
        )
        query = render_query(TREE_FIELDS, kind="DiffTree", filters=args)
        data = await self.api.graphql.execute(query, idempotent=True)
        return data.get("DiffTree")

    async def summary(
        self,
        branch: str,
        *,
        from_time: str | None = None,
        to_time: str | None = None,
        name: str | None = None,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """The counts behind a branch's diff, without the nodes.

        See tree() for the arguments; this one takes no paging, since
        DiffTreeSummary answers with nothing to page through.

        Returns:
            The DiffTreeSummary payload (the two branch names, the window
            and the num_* counts), or None when the server holds no diff
            for that branch.
        """
        args = self._args(branch, from_time, to_time, name, filters)
        query = render_query(SUMMARY_FIELDS, kind="DiffTreeSummary", filters=args)
        data = await self.api.graphql.execute(query, idempotent=True)
        return data.get("DiffTreeSummary")

    async def files(self, branch: str) -> Any:
        """Files that differ between a branch and the default branch.

        This and artifacts() are the only parts of a diff served over
        REST; everything else about one is GraphQL.

        Args:
            branch: The branch to compare.

        Returns:
            The route's decoded JSON, keyed by repository.
        """
        return await self.api._request(
            "GET", f"{self.api.base_url}/api/diff/files", params={"branch": branch}
        )

    async def artifacts(self, branch: str) -> Any:
        """Artifacts that differ between a branch and the default branch.

        Args:
            branch: The branch to compare.

        Returns:
            The route's decoded JSON, keyed by artifact id.
        """
        return await self.api._request(
            "GET",
            f"{self.api.base_url}/api/diff/artifacts",
            params={"branch": branch},
        )
