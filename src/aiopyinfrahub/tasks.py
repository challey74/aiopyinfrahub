"""Tasks: Infrahub's server-side work queue, exposed only as GraphQL."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from aiopyinfrahub.exceptions import TaskTimeoutError
from aiopyinfrahub.graphql import EnumValue, render_query
from aiopyinfrahub.response import Record

if TYPE_CHECKING:
    from aiopyinfrahub.api import Api

# TaskNode's scalar fields on 1.10 (verified against the sandbox SDL).
# `logs` and `related_nodes` are nested objects, and `related_node` and
# `related_node_kind` are deprecated in favor of the latter, so none of
# the four are selected.
TASK_FIELDS: dict[str, Any] = {
    "id": None,
    "title": None,
    "state": None,
    "conclusion": None,
    "progress": None,
    "workflow": None,
    "branch": None,
    "created_at": None,
    "updated_at": None,
    "start_time": None,
    "tags": None,
}

# The StateType values that mean the task has not settled yet; the rest
# (COMPLETED, CRASHED, FAILED, CANCELLED) are terminal.
ACTIVE_STATES = frozenset({"PENDING", "RUNNING", "SCHEDULED", "PAUSED", "CANCELLING"})

# InfrahubTask declares `state: [StateType]`, so its values render as bare
# enum tokens; everything else the query takes is a String or an ID.
ENUM_FILTERS = frozenset({"state"})


def _enums(filters: dict[str, Any]) -> dict[str, Any]:
    """Mark enum-typed filter values so the renderer leaves them unquoted."""
    marked = dict(filters)
    for key in ENUM_FILTERS & marked.keys():
        value = marked[key]
        marked[key] = (
            [EnumValue(v) for v in value]
            if isinstance(value, list)
            else EnumValue(value)
        )
    return marked


class TaskSet:
    """Lazy async iterable of task Records.

    Nothing is fetched until iteration starts, and each `async for`
    re-runs the query. InfrahubTask paginates like a node query
    (offset/limit with a `count`), but the pages are walked in order
    rather than fanned out: a task list is short, and states move under a
    reader that is usually looking for the newest entries anyway.
    """

    def __init__(self, api: Api, filters: dict[str, Any] | None = None) -> None:
        self.api = api
        self.filters = filters or {}

    def __aiter__(self) -> AsyncGenerator[Record]:
        return self._iter()

    def _query(self, fields: dict[str, Any], **page: Any) -> str:
        return render_query(
            fields, kind="InfrahubTask", filters={**_enums(self.filters), **page}
        )

    async def _iter(self) -> AsyncGenerator[Record]:
        offset = 0
        while True:
            query = self._query(
                {"count": None, "edges": {"node": TASK_FIELDS}},
                offset=offset,
                limit=self.api.page_size,
            )
            data = await self.api.graphql.execute(query, idempotent=True)
            page = data.get("InfrahubTask") or {}
            edges = page.get("edges") or []
            for edge in edges:
                yield Record(edge["node"], self.api, full=True)
            # The served page size, not the requested one, drives the next
            # offset, exactly as in RecordSet.
            offset += len(edges)
            if not edges or offset >= (page.get("count") or 0):
                return

    async def count(self) -> int:
        """Total task count for the query, in one limit=1 query."""
        data = await self.api.graphql.execute(
            self._query({"count": None}, limit=1), idempotent=True
        )
        return (data.get("InfrahubTask") or {}).get("count") or 0


class Tasks:
    """ih.tasks: read and wait on Infrahub's server-side tasks.

    Task records are plain Records: a task is not a schema kind, its
    fields carry no attribute or relationship wrappers to flatten, and
    nothing mutates one, so save() and delete() on one raise ValueError.
    """

    def __init__(self, api: Api) -> None:
        self.api = api

    def all(self) -> TaskSet:
        """Every task the server still holds, as a lazy iterable."""
        return TaskSet(self.api)

    def filter(self, **filters: Any) -> TaskSet:
        """Tasks matching the InfrahubTask query's filters, lazily.

        Args:
            **filters: `ids=`, `state=` (StateType names, e.g.
                `["RUNNING"]`), `workflow=`, `branch=`,
                `related_node__ids=`, and `q=` for free text. These come
                from the hand-written InfrahubTask query, not from a node
                schema, so the `<attr>__value` spelling does not apply.
        """
        return TaskSet(self.api, filters)

    async def get(self, task_id: str) -> Record | None:
        """One task by id, or None if the server has no such task."""
        async for task in TaskSet(self.api, {"ids": [task_id]}):
            return task
        return None

    async def count(self, **filters: Any) -> int:
        """Task count for the given filters (every task if none)."""
        return await TaskSet(self.api, filters).count()

    async def wait(
        self,
        task_id: str,
        # ASYNC109 wants asyncio.timeout() around the call instead. This is
        # a poll loop rather than one cancellable await, and the deadline
        # belongs in the signature callers read, so the parameter stays.
        timeout: float = 60.0,  # noqa: ASYNC109
        interval: float = 1.0,
    ) -> Record:
        """Poll a task until it leaves the active states.

        Args:
            task_id: The task to poll, e.g. the id a branch operation
                called with wait=False returned.
            timeout: Seconds to poll before giving up.
            interval: Seconds between polls.

        Returns:
            The task Record in its final state.

        Raises:
            TaskTimeoutError: If the task is still active at `timeout`.
            ValueError: If the server has no task with that id.
        """
        deadline = time.monotonic() + timeout
        while True:
            task = await self.get(task_id)
            if task is None:
                raise ValueError(f"No task with id {task_id!r}.")
            if task.state not in ACTIVE_STATES:
                return task
            # Checked before sleeping, so the deadline is not overshot by
            # a whole interval before it is noticed.
            if time.monotonic() + interval > deadline:
                raise TaskTimeoutError(task_id, timeout)
            await asyncio.sleep(interval)
