import json

import pytest


def last_query(fake):
    return json.loads(fake.requests[-1].content)["query"]


async def test_list(ih):
    branches = await ih.branches.list()
    assert sorted(str(b) for b in branches) == ["feature-x", "main"]


async def test_list_uses_the_flat_query(ih, fake):
    """The top-level Branch query is a list, not count/edges/node."""
    await ih.branches.list()
    query = last_query(fake)
    assert "edges" not in query
    assert "graph_version" in query


async def test_list_selects_no_version_fragile_fields(ih, fake):
    """schema_differs_from_default_branch exists only on develop as of 1.10
    and has_schema_changes is deprecated, so neither may be selected."""
    await ih.branches.list()
    query = last_query(fake)
    assert "schema_differs_from_default_branch" not in query
    assert "has_schema_changes" not in query


async def test_list_needs_no_schema(ih, fake):
    """A branch is not a schema kind, so nothing fetches /api/schema."""
    await ih.branches.list()
    assert not [r for r in fake.requests if r.url.path == "/api/schema"]


async def test_get(ih):
    branch = await ih.branches.get("feature-x")
    assert branch is not None
    assert branch.description == "wip"
    assert branch.is_default is False


async def test_get_unknown_returns_none(ih):
    assert await ih.branches.get("nope") is None


async def test_create(ih, fake):
    branch = await ih.branches.create("feature-y", description="new work")
    assert branch.name == "feature-y"
    assert branch.description == "new work"
    assert "feature-y" in fake.branches


async def test_create_waits_by_default(ih, fake):
    await ih.branches.create("feature-y")
    query = last_query(fake)
    assert "wait_until_completion: true" in query
    assert "sync_with_git: false" in query


async def test_create_without_waiting_returns_a_task_id(ih, fake):
    """The object would be a snapshot of work not yet done, so wait=False
    selects the queued task's id instead."""
    task_id = await ih.branches.create("feature-z", wait=False)
    query = last_query(fake)
    assert "wait_until_completion: false" in query
    assert "task {" in query
    assert "object" not in query
    assert task_id in fake.tasks


async def test_a_wait_false_task_id_feeds_tasks_wait(ih, fake):
    task_id = await ih.branches.merge("feature-x", wait=False)
    assert isinstance(task_id, str)
    task = await ih.tasks.wait(task_id, interval=0)
    assert task.state == "COMPLETED"


async def test_delete_without_waiting_returns_a_task_id(ih, fake):
    task_id = await ih.branches.delete("feature-x", wait=False)
    assert task_id in fake.tasks
    assert "feature-x" not in fake.branches


async def test_rebase_without_waiting_returns_a_task_id(ih, fake):
    assert await ih.branches.rebase("feature-x", wait=False) in fake.tasks


async def test_validate_without_waiting_returns_a_task_id(ih, fake):
    assert await ih.branches.validate("feature-x", wait=False) in fake.tasks


async def test_delete(ih, fake):
    assert await ih.branches.delete("feature-x") is True
    assert "feature-x" not in fake.branches
    # BranchDelete has no `object` in its payload, only ok and task.
    assert "object" not in last_query(fake)


async def test_update_returns_ok_only(ih, fake):
    """BranchUpdate answers with ok and takes no wait_until_completion."""
    assert await ih.branches.update("feature-x", "changed") is True
    query = last_query(fake)
    assert "wait_until_completion" not in query
    assert "object" not in query
    branch = await ih.branches.get("feature-x")
    assert branch is not None
    assert branch.description == "changed"


async def test_merge(ih):
    branch = await ih.branches.merge("feature-x")
    assert branch.status == "MERGED"


async def test_rebase(ih, fake):
    branch = await ih.branches.rebase("feature-x")
    assert branch.name == "feature-x"
    assert "BranchRebase" in last_query(fake)


async def test_validate(ih, fake):
    branch = await ih.branches.validate("feature-x")
    assert branch.name == "feature-x"
    assert "BranchValidate" in last_query(fake)


async def test_unknown_branch_raises(ih):
    import aiopyinfrahub

    with pytest.raises(aiopyinfrahub.GraphQLError, match="not found"):
        await ih.branches.merge("nope")


async def test_branch_records_carry_no_kind(ih):
    """A branch has no <Kind>Update mutation behind it, so save() refuses."""
    branch = await ih.branches.get("feature-x")
    assert branch is not None
    branch.description = "local only"
    with pytest.raises(ValueError, match="no id and __typename"):
        await branch.save()
