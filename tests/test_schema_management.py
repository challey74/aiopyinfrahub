import json

import pytest
from conftest import SCHEMA, SCHEMA_DIFF, make_api

import aiopyinfrahub

RACK = {"version": "1.0", "nodes": [{"name": "Rack", "namespace": "Testing"}]}


def last_query(fake):
    return json.loads(fake.requests[-1].content)["query"]


def schema_reads(fake):
    return [r for r in fake.requests if r.url.path == "/api/schema"]


async def test_load_schemas_posts_the_documents(ih, fake):
    result = await ih.load_schemas([RACK])
    assert result["schema_updated"] is True
    assert result["previous_hash"] == SCHEMA["main"]
    assert result["diff"] == SCHEMA_DIFF
    request = fake.requests[-1]
    assert request.url.path == "/api/schema/load"
    assert json.loads(request.content) == {"schemas": [RACK]}
    assert fake.loaded_schemas == [RACK]


async def test_load_schemas_drops_that_branch_from_the_cache(ih, fake):
    await ih.schema()
    await ih.load_schemas([RACK])
    await ih.schema()
    assert len(schema_reads(fake)) == 2


async def test_load_schemas_leaves_other_branches_cached(ih, fake):
    await ih.schema(branch="feature-x")
    await ih.load_schemas([RACK], branch="main")
    await ih.schema(branch="feature-x")
    assert len(schema_reads(fake)) == 1


async def test_load_schemas_passes_the_branch(ih, fake):
    await ih.load_schemas([RACK], branch="feature-x")
    assert fake.requests[-1].url.params["branch"] == "feature-x"


async def test_the_client_branch_is_the_default(fake):
    async with make_api(fake, branch="feature-x") as ih:
        await ih.load_schemas([RACK])
    assert fake.requests[-1].url.params["branch"] == "feature-x"


async def test_the_refetched_schema_carries_the_new_hash(ih, fake):
    await ih.load_schemas([RACK])
    await ih.schema()
    assert fake.schema_hash != SCHEMA["main"]


async def test_check_schemas_returns_the_202_body(ih, fake):
    result = await ih.check_schemas([RACK])
    assert result["diff"] == SCHEMA_DIFF
    assert result["warnings"] == []
    assert fake.requests[-1].url.path == "/api/schema/check"
    assert json.loads(fake.requests[-1].content) == {"schemas": [RACK]}


async def test_check_schemas_changes_nothing(ih, fake):
    await ih.schema()
    await ih.check_schemas([RACK])
    await ih.schema()
    assert len(schema_reads(fake)) == 1
    assert fake.loaded_schemas == []


async def test_schema_in_sync_reads_infrahub_status(ih, fake):
    assert await ih.schema_in_sync() is True
    assert "schema_hash_synced" in last_query(fake)
    assert fake.requests[-1].url.path == "/graphql"


async def test_schema_in_sync_is_false_while_workers_catch_up(ih, fake):
    fake.sync_after_polls = 5
    assert await ih.schema_in_sync() is False


async def test_wait_schemas_converged_polls_until_synced(ih, fake):
    fake.sync_after_polls = 2
    await ih.wait_schemas_converged(interval=0)
    assert fake.status_polls == 3


async def test_wait_schemas_converged_returns_at_once_when_synced(ih, fake):
    await ih.wait_schemas_converged(interval=0)
    assert fake.status_polls == 1


async def test_wait_schemas_converged_times_out(ih, fake):
    fake.sync_after_polls = 99
    with pytest.raises(aiopyinfrahub.ConvergenceTimeoutError) as excinfo:
        await ih.wait_schemas_converged(timeout=0, interval=0)
    assert excinfo.value.timeout == 0


async def test_a_load_restarts_convergence(ih, fake):
    """The workers adopt a new schema on their own schedule."""
    fake.sync_after_polls = 1
    assert await ih.schema_in_sync() is False
    assert await ih.schema_in_sync() is True
    await ih.load_schemas([RACK])
    assert await ih.schema_in_sync() is False
