"""Branches: the branch lifecycle, which Infrahub exposes only as GraphQL."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiopyinfrahub.graphql import render_mutation, render_query
from aiopyinfrahub.response import Record

if TYPE_CHECKING:
    from aiopyinfrahub.api import Api

# BranchType fields present on current stable servers (verified against
# Infrahub 1.10.6 on sandbox.infrahub.app). `is_isolated` and
# `has_schema_changes` are deprecated (removal planned for 1.14), and their
# replacement `schema_differs_from_default_branch` exists only on develop as
# of 1.10, so selecting any of the three breaks one end of the supported
# range. None are worth that.
BRANCH_FIELDS: dict[str, Any] = {
    "id": None,
    "name": None,
    "description": None,
    "origin_branch": None,
    "branched_from": None,
    "status": None,
    "sync_with_git": None,
    "is_default": None,
    "created_at": None,
    "graph_version": None,
}


def _task_id(result: dict[str, Any]) -> str:
    """The queued task's id, which is all a wait=False payload is good for."""
    return (result.get("task") or {}).get("id") or ""


class Branches:
    """ih.branches: list and manage branches.

    Branch records are plain Records: a branch is not a schema kind, its
    query answers with a flat list rather than count/edges/node, and its
    payload carries no attribute or relationship wrappers to flatten. They
    have no kind, so save() and delete() on one raise ValueError; use the
    methods here instead.
    """

    def __init__(self, api: Api) -> None:
        self.api = api

    def _record(self, values: dict[str, Any]) -> Record:
        return Record(values, self.api, full=True)

    async def _query(self, filters: dict[str, Any] | None = None) -> list[Record]:
        # The top-level Branch query is a real special case: a flat list,
        # not the paginated count/edges/node shape every kind uses.
        query = render_query(BRANCH_FIELDS, kind="Branch", filters=filters)
        data = await self.api.graphql.execute(query, idempotent=True)
        return [self._record(branch) for branch in data.get("Branch") or []]

    async def _mutate(
        self,
        name: str,
        data: dict[str, Any],
        fields: dict[str, Any],
        wait: bool | None,
    ) -> dict[str, Any]:
        # wait_until_completion sits beside `data`, not inside it; passing
        # False returns as soon as the server-side task is queued, so the
        # object would be a snapshot of work not yet done and the task id
        # is the only part of the payload worth selecting.
        extra = None if wait is None else {"wait_until_completion": wait}
        if wait is False:
            fields = {"ok": None, "task": {"id": None}}
        query = render_mutation(fields, name=name, data=data, extra_args=extra)
        result = await self.api.graphql.execute(query)
        return result.get(name) or {}

    async def list(self) -> list[Record]:
        """Every branch on the instance."""
        return await self._query()

    async def get(self, name: str) -> Record | None:
        """One branch by name, or None if it does not exist."""
        branches = await self._query({"name": name})
        return branches[0] if branches else None

    async def create(
        self,
        name: str,
        *,
        description: str | None = None,
        sync_with_git: bool = False,
        wait: bool = True,
    ) -> Record | str:
        """Create a branch.

        Args:
            name: The new branch's name.
            description: Free-text description.
            sync_with_git: Whether the branch is pushed to linked git
                repositories. False keeps it database-only.
            wait: Wait for the server-side task to finish. False returns
                once it is queued, so the branch may not exist yet.

        Returns:
            The new branch, or with wait=False the id of the queued task,
            to hand to `ih.tasks.wait()`.
        """
        data: dict[str, Any] = {"name": name, "sync_with_git": sync_with_git}
        if description is not None:
            data["description"] = description
        result = await self._mutate(
            "BranchCreate", data, {"ok": None, "object": BRANCH_FIELDS}, wait
        )
        if not wait:
            return _task_id(result)
        return self._record(result.get("object") or {})

    async def delete(self, name: str, wait: bool = True) -> bool | str:
        """Delete a branch.

        Returns:
            The mutation's `ok` flag, or with wait=False the id of the
            queued task, to hand to `ih.tasks.wait()`. BranchDelete
            answers with no object either way.
        """
        result = await self._mutate("BranchDelete", {"name": name}, {"ok": None}, wait)
        if not wait:
            return _task_id(result)
        return bool(result.get("ok"))

    async def update(self, name: str, description: str) -> bool:
        """Set a branch's description.

        Returns:
            The mutation's `ok` flag. BranchUpdate returns only `ok`, and
            unlike the others takes no wait_until_completion, so re-read
            the branch with get() to see the new value.
        """
        result = await self._mutate(
            "BranchUpdate",
            {"name": name, "description": description},
            {"ok": None},
            None,
        )
        return bool(result.get("ok"))

    async def rebase(self, name: str, wait: bool = True) -> Record | str:
        """Rebase a branch onto the default branch.

        Returns:
            The branch, or with wait=False the id of the queued task, to
            hand to `ih.tasks.wait()`.
        """
        return await self._branch_task("BranchRebase", name, wait)

    async def merge(self, name: str, wait: bool = True) -> Record | str:
        """Merge a branch into the default branch.

        Returns:
            The branch, or with wait=False the id of the queued task, to
            hand to `ih.tasks.wait()`.
        """
        return await self._branch_task("BranchMerge", name, wait)

    async def validate(self, name: str, wait: bool = True) -> Record | str:
        """Check a branch for conflicts against the default branch.

        Returns:
            The branch, or with wait=False the id of the queued task, to
            hand to `ih.tasks.wait()`.
        """
        return await self._branch_task("BranchValidate", name, wait)

    async def _branch_task(self, mutation: str, name: str, wait: bool) -> Record | str:
        result = await self._mutate(
            mutation, {"name": name}, {"ok": None, "object": BRANCH_FIELDS}, wait
        )
        if not wait:
            return _task_id(result)
        return self._record(result.get("object") or {})
