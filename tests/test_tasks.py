import json

import pytest
from conftest import TASK_ID_PREFIX, make_api

import aiopyinfrahub

TASK_IDS = [f"{TASK_ID_PREFIX}{i:02d}" for i in range(1, 5)]


def last_query(fake):
    return json.loads(fake.requests[-1].content)["query"]


@pytest.fixture
def tasked(fake):
    fake.add_task(TASK_IDS[0], title="merge feature-x", states=("COMPLETED",))
    fake.add_task(TASK_IDS[1], title="rebase feature-x", states=("RUNNING",))
    return fake


async def test_all_is_lazy(tasked, ih):
    tasks = ih.tasks.all()
    assert not tasked.requests
    assert [t.title async for t in tasks] == ["merge feature-x", "rebase feature-x"]


async def test_task_fields(tasked, ih):
    task = await ih.tasks.get(TASK_IDS[0])
    assert task is not None
    assert task.state == "COMPLETED"
    assert task.workflow == "branch_create"
    assert task.branch == "main"


async def test_get_unknown_returns_none(tasked, ih):
    assert await ih.tasks.get(f"{TASK_ID_PREFIX}99") is None


async def test_count(tasked, ih, fake):
    assert await ih.tasks.count() == 2
    assert "limit: 1" in last_query(fake)


async def test_filter_state_renders_a_bare_enum(tasked, ih, fake):
    """InfrahubTask declares `state: [StateType]`, and graphene rejects a
    quoted literal where an enum is declared."""
    titles = [t.title async for t in ih.tasks.filter(state=["RUNNING"])]
    assert titles == ["rebase feature-x"]
    assert "state: [RUNNING]" in last_query(fake)


async def test_pagination_walks_every_page(tasked, fake):
    for task_id in TASK_IDS[2:]:
        fake.add_task(task_id)
    async with make_api(fake, page_size=1) as ih:
        assert len([t async for t in ih.tasks.all()]) == 4


async def test_tasks_need_no_schema(tasked, ih, fake):
    """A task is not a schema kind, so nothing fetches /api/schema."""
    await ih.tasks.count()
    assert not [r for r in fake.requests if r.url.path == "/api/schema"]


async def test_wait_polls_until_the_state_settles(fake, ih):
    fake.add_task(TASK_IDS[0], states=("PENDING", "RUNNING", "COMPLETED"))
    task = await ih.tasks.wait(TASK_IDS[0], interval=0)
    assert task.state == "COMPLETED"
    assert len(fake.requests) == 3


async def test_wait_returns_immediately_when_already_settled(fake, ih):
    fake.add_task(TASK_IDS[0], states=("FAILED",))
    task = await ih.tasks.wait(TASK_IDS[0], interval=0)
    assert task.state == "FAILED"


async def test_wait_times_out(fake, ih):
    fake.add_task(TASK_IDS[0], states=("RUNNING",))
    with pytest.raises(aiopyinfrahub.TaskTimeoutError) as excinfo:
        await ih.tasks.wait(TASK_IDS[0], timeout=0, interval=0)
    assert excinfo.value.task_id == TASK_IDS[0]
    assert excinfo.value.timeout == 0


async def test_wait_on_an_unknown_task_raises(ih):
    with pytest.raises(ValueError, match="No task with id"):
        await ih.tasks.wait(TASK_IDS[0], interval=0)


async def test_task_records_carry_no_kind(tasked, ih):
    """Nothing mutates a task, so save() on one refuses."""
    task = await ih.tasks.get(TASK_IDS[0])
    assert task is not None
    task.title = "local only"
    with pytest.raises(ValueError, match="no id and __typename"):
        await task.save()
