import json

import pytest
from conftest import (
    IP_POOL_ID,
    IP_RESOURCE_ID,
    PREFIX_POOL_ID,
    PREFIX_RESOURCE_ID,
    parse_query,
)

import aiopyinfrahub
from aiopyinfrahub.response import Record

UNKNOWN_POOL_ID = "88888888-8888-4888-8888-888888888899"


def last_query(fake):
    return json.loads(fake.requests[-1].content)["query"]


async def test_next_ip_address_allocates_from_the_pool(ih, fake):
    record = await ih.pools.next_ip_address(IP_POOL_ID)
    assert isinstance(record, Record)
    assert record.display_label == "10.0.0.1/24"
    assert record.kind == "IpamIPAddress"
    assert fake.pools[IP_POOL_ID]["available"] == ["10.0.0.2/24", "10.0.0.3/24"]


async def test_allocation_is_not_repeated(ih):
    first = await ih.pools.next_ip_address(IP_POOL_ID)
    second = await ih.pools.next_ip_address(IP_POOL_ID)
    assert first.display_label != second.display_label


async def test_next_ip_address_sends_the_pool_input(ih, fake):
    await ih.pools.next_ip_address(
        IP_POOL_ID,
        identifier="loopback",
        prefix_length=32,
        data={"description": "lo0"},
    )
    _, name, args, _ = parse_query(last_query(fake))
    assert name == "InfrahubIPAddressPoolGetResource"
    assert args["data"] == {
        "id": IP_POOL_ID,
        "identifier": "loopback",
        "prefix_length": 32,
        "data": {"description": "lo0"},
    }


async def test_next_ip_address_omits_unset_options(ih, fake):
    await ih.pools.next_ip_address(IP_POOL_ID)
    _, _, args, _ = parse_query(last_query(fake))
    assert args["data"] == {"id": IP_POOL_ID}


async def test_a_pool_may_be_given_as_a_record(ih):
    pool = Record({"id": IP_POOL_ID}, ih)
    assert (await ih.pools.next_ip_address(pool)).display_label == "10.0.0.1/24"


async def test_a_pool_record_without_an_id_is_rejected(ih):
    with pytest.raises(ValueError, match="neither an id nor a Record"):
        await ih.pools.next_ip_address(Record({"name": "management"}, ih))


async def test_next_ip_prefix_uses_the_prefix_mutation(ih, fake):
    record = await ih.pools.next_ip_prefix(PREFIX_POOL_ID, prefix_length=24)
    assert record.display_label == "10.1.0.0/24"
    _, name, args, _ = parse_query(last_query(fake))
    assert name == "InfrahubIPPrefixPoolGetResource"
    assert args["data"]["prefix_length"] == 24


async def test_exhausting_a_pool_raises(ih):
    """The server answers an empty pool with a 200 carrying errors."""
    await ih.pools.next_ip_prefix(PREFIX_POOL_ID)
    await ih.pools.next_ip_prefix(PREFIX_POOL_ID)
    with pytest.raises(aiopyinfrahub.GraphQLError, match="No available resource"):
        await ih.pools.next_ip_prefix(PREFIX_POOL_ID)


async def test_an_unknown_pool_raises(ih):
    with pytest.raises(aiopyinfrahub.GraphQLError, match="was not found"):
        await ih.pools.next_ip_address(UNKNOWN_POOL_ID)


async def test_allocated_records_are_brief(ih):
    """PoolAllocatedNode describes the allocation, not the node."""
    record = await ih.pools.next_ip_address(IP_POOL_ID)
    with pytest.raises(AttributeError, match="full_details"):
        record.address  # noqa: B018


async def test_the_record_is_stamped_with_the_allocation_branch(ih):
    record = await ih.pools.next_ip_address(IP_POOL_ID)
    assert record._branch == "main"


async def test_utilization_reports_the_pool_and_its_resources(ih):
    await ih.pools.next_ip_address(IP_POOL_ID)
    report = await ih.pools.utilization(IP_POOL_ID)
    assert report["count"] == 1
    assert report["utilization"] == pytest.approx(100 / 3)
    resource = report["edges"][0]["node"]
    assert resource["id"] == IP_RESOURCE_ID
    assert resource["weight"] == 3


async def test_utilization_of_an_untouched_pool_is_zero(ih):
    assert (await ih.pools.utilization(PREFIX_POOL_ID))["utilization"] == 0.0


async def test_utilization_of_an_unknown_pool_raises(ih):
    with pytest.raises(aiopyinfrahub.GraphQLError, match="was not found"):
        await ih.pools.utilization(UNKNOWN_POOL_ID)


async def test_allocated_lists_what_the_pool_handed_out(ih):
    await ih.pools.next_ip_address(IP_POOL_ID, identifier="lo0")
    await ih.pools.next_ip_address(IP_POOL_ID, identifier="lo1")
    allocated = await ih.pools.allocated(IP_POOL_ID, IP_RESOURCE_ID)
    assert [a["identifier"] for a in allocated] == ["lo0", "lo1"]
    assert allocated[0]["display_label"] == "10.0.0.1/24"
    assert allocated[0]["branch"] == "main"


async def test_allocated_pages(ih, fake):
    for index in range(3):
        await ih.pools.next_ip_address(IP_POOL_ID, identifier=f"lo{index}")
    allocated = await ih.pools.allocated(IP_POOL_ID, IP_RESOURCE_ID, offset=1, limit=1)
    assert [a["identifier"] for a in allocated] == ["lo1"]
    _, _, args, _ = parse_query(last_query(fake))
    assert args["offset"] == 1
    assert args["limit"] == 1


async def test_allocated_omits_unset_paging(ih, fake):
    await ih.pools.allocated(IP_POOL_ID, IP_RESOURCE_ID)
    _, _, args, _ = parse_query(last_query(fake))
    assert args == {"pool_id": IP_POOL_ID, "resource_id": IP_RESOURCE_ID}


async def test_allocated_needs_the_right_resource(ih):
    """InfrahubResourcePoolAllocated declares resource_id: String!, so
    the resource is required rather than pool-wide."""
    with pytest.raises(aiopyinfrahub.GraphQLError, match="is not in pool"):
        await ih.pools.allocated(IP_POOL_ID, PREFIX_RESOURCE_ID)
