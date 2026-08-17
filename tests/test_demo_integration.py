"""Live integration tests against a real Infrahub.

Skipped unless AIOPYINFRAHUB_DEMO_URL is set, so the default suite stays
offline. The public sandbox serves anonymous reads, so no token is needed
for the read tests:

    AIOPYINFRAHUB_DEMO_URL=https://sandbox.infrahub.app uv run pytest tests/test_demo_integration.py

The sandbox rejects every mutation (403), and it is shared, so the write
tests want your own instance. The docker demo is one command:

    curl https://infrahub.opsmill.io > docker-compose.yml
    docker compose -p infrahub up -d

That compose file's default admin token is
06438eb2-8019-4776-878c-0941b1f1d1ec, so:

    AIOPYINFRAHUB_DEMO_URL=http://localhost:8000 \
    AIOPYINFRAHUB_DEMO_TOKEN=06438eb2-8019-4776-878c-0941b1f1d1ec \
    AIOPYINFRAHUB_DEMO_WRITES=1 uv run pytest tests/test_demo_integration.py

The write tests create uniquely named objects and delete them afterward,
but only opt in on an instance whose data is disposable.

The sandbox's dataset changes and no reset schedule is published, so
nothing here may assert an exact object count or a dataset-specific name.
"""

import os
import uuid

import pytest

import aiopyinfrahub

DEMO_URL = os.environ.get("AIOPYINFRAHUB_DEMO_URL")
# No default token: Infrahub allows anonymous reads, and the public sandbox
# is configured that way, so the read tests need no credentials at all.
DEMO_TOKEN = os.environ.get("AIOPYINFRAHUB_DEMO_TOKEN")
DEMO_WRITES = os.environ.get("AIOPYINFRAHUB_DEMO_WRITES") == "1"

pytestmark = pytest.mark.skipif(
    not DEMO_URL, reason="AIOPYINFRAHUB_DEMO_URL not set; live tests are opt-in"
)


@pytest.fixture
async def live():
    async with aiopyinfrahub.api(DEMO_URL, token=DEMO_TOKEN, timeout=60) as ih:
        yield ih


async def test_version(live):
    assert await live.version()


async def test_schema_is_cached_per_client(live):
    """Every instance carries the Builtin and Core kinds, and the second
    schema() call is served from the per-branch cache: the same object,
    not an equal copy."""
    schema = await live.schema()
    assert "BuiltinTag" in schema
    assert "CoreRepository" in schema
    assert await live.schema() is schema


async def test_count(live):
    assert await live.BuiltinTag.count() >= 0


async def test_iterate_and_get_round_trip(live):
    """A page of records, then both get() paths back to one of them: by
    UUID and by filter. Identity is (branch, id), so the round trip
    compares equal to the record the iteration yielded."""
    records = []
    async for tag in live.BuiltinTag.all(limit=5):
        records.append(tag)
        if len(records) == 3:
            break  # bounded: an early break stops the paging
    if not records:
        pytest.skip("no BuiltinTag objects on instance")
    for tag in records:
        assert tag.id
        assert tag.display_label
        assert tag.__typename == "BuiltinTag"

    first = records[0]
    by_id = await live.BuiltinTag.get(first.id)
    assert by_id is not None
    assert by_id == first

    by_name = await live.BuiltinTag.get(name__value=first.name)
    assert by_name is not None
    assert by_name.id == first.id


async def test_branches_list(live):
    """Exactly one branch is the default, whatever else exists."""
    branches = await live.branches.list()
    assert branches
    assert len([b for b in branches if b.is_default]) == 1


async def test_raw_graphql(live):
    """The flat top-level Branch query, hand-written rather than rendered."""
    result = await live.graphql.query("query { Branch { name } }")
    assert result.errors == []
    assert result.data["Branch"]


needs_writes = pytest.mark.skipif(
    not (DEMO_WRITES and DEMO_TOKEN),
    reason="writes need AIOPYINFRAHUB_DEMO_WRITES=1 and AIOPYINFRAHUB_DEMO_TOKEN; "
    "Infrahub rejects anonymous mutations",
)


@needs_writes
async def test_tag_lifecycle(live):
    """create -> diff save -> delete on a uniquely named tag. Cleanup runs
    in finally so a failed assertion cannot leave the tag behind."""
    name = f"aiopyih-it-{uuid.uuid4().hex[:8]}"
    tag = await live.BuiltinTag.create(name=name)
    try:
        assert tag.id
        assert tag.name == name

        tag.description = "aiopyinfrahub integration test"
        assert await tag.save() is True
        assert await tag.save() is False  # clean record sends nothing

        refetched = await live.BuiltinTag.get(tag.id)
        assert refetched is not None
        assert refetched.description == "aiopyinfrahub integration test"
    finally:
        assert await tag.delete() is True
        assert await live.BuiltinTag.get(name__value=name) is None


@needs_writes
async def test_branch_lifecycle(live):
    """create -> read -> delete on a uniquely named branch, cleaned up in
    finally. A created branch is never the default one."""
    name = f"aiopyih-it-{uuid.uuid4().hex[:8]}"
    branch = await live.branches.create(name, description="aiopyinfrahub test")
    try:
        assert branch.name == name
        listed = await live.branches.get(name)
        assert listed is not None
        assert listed.is_default is False
    finally:
        assert await live.branches.delete(name) is True
        assert await live.branches.get(name) is None
