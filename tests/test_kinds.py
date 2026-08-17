import json

import pytest
from conftest import DEVICE_IDS, SITE_ID, TAG_IDS, make_api, parse_query

from aiopyinfrahub.kinds import KindEndpoint
from aiopyinfrahub.response import Record


def last_query(fake):
    return json.loads(fake.requests[-1].content)["query"]


def test_attribute_access_builds_a_kind_endpoint(ih):
    endpoint = ih.TestingDevice
    assert isinstance(endpoint, KindEndpoint)
    assert endpoint.name == "TestingDevice"


def test_attribute_access_does_no_io(ih, fake):
    ih.TestingDevice  # noqa: B018
    assert not fake.requests


def test_attribute_access_builds_a_fresh_endpoint(ih):
    """Like the sisters' Endpoint, so nothing caches across accesses."""
    assert ih.TestingDevice is not ih.TestingDevice


def test_private_attribute_raises(ih):
    with pytest.raises(AttributeError):
        ih._nope  # noqa: B018


def test_kind_escape_hatch(ih):
    assert ih.kind("TestingDevice").name == "TestingDevice"


async def test_unknown_kind_suggests_close_matches(ih):
    """Kinds are instance-specific, so a typo must not just 404 server-side."""
    with pytest.raises(ValueError, match="Did you mean: TestingDevice"):
        await ih.TestingDevic.get("sw-1")


async def test_get_by_uuid(ih, fake):
    device = await ih.TestingDevice.get(DEVICE_IDS[0])
    assert device is not None
    assert device.name == "sw-1"
    assert f'ids: ["{DEVICE_IDS[0]}"]' in last_query(fake)


async def test_get_by_default_filter(ih, fake):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    assert device.id == DEVICE_IDS[0]
    assert 'name__value: "sw-1"' in last_query(fake)


async def test_get_by_filter(ih):
    device = await ih.TestingDevice.get(serial__value="ABC123")
    assert device is not None
    assert device.name == "sw-1"


async def test_get_by_hfid(ih):
    device = await ih.TestingDevice.get(hfid=["sw-1"])
    assert device is not None
    assert device.hfid == ["sw-1"]


async def test_get_missing_returns_none(ih):
    assert await ih.TestingDevice.get("nope") is None


async def test_get_matching_many_raises(ih):
    # sw-2 through sw-5 all have an empty serial.
    with pytest.raises(ValueError, match="more than one result"):
        await ih.TestingDevice.get(serial__value="")


async def test_get_without_a_default_filter_raises(ih):
    """TestingInterface declares none, so a bare positional is ambiguous."""
    with pytest.raises(ValueError, match="declares no default_filter"):
        await ih.TestingInterface.get("Ethernet1")


def test_filter_requires_filters(ih):
    with pytest.raises(ValueError, match="Use all\\(\\) instead"):
        ih.TestingDevice.filter()


def test_all_offset_requires_limit(ih):
    with pytest.raises(ValueError, match="offset requires a positive limit"):
        ih.TestingDevice.all(offset=10)


async def test_count(ih, fake):
    assert await ih.TestingDevice.count() == 5
    assert await ih.TestingDevice.count(name__value="sw-1") == 1
    assert "limit: 1" in last_query(fake)


async def test_create_wraps_scalars_by_schema(ih, fake):
    device = await ih.TestingDevice.create(
        name="sw-new", serial="NEW1", site=SITE_ID, tags=TAG_IDS
    )
    assert isinstance(device, Record)
    assert device.name == "sw-new"
    assert device.site.display_label == "atl1"
    assert [str(t) for t in device.tags] == ["prod", "edge"]
    _, name, args, _ = parse_query(last_query(fake))
    assert name == "TestingDeviceCreate"
    assert args["data"] == {
        "name": {"value": "sw-new"},
        "serial": {"value": "NEW1"},
        "site": {"id": SITE_ID},
        "tags": [{"id": TAG_IDS[0]}, {"id": TAG_IDS[1]}],
    }


async def test_create_passes_the_wire_shape_through(ih, fake):
    """Callers can always spell the full input, e.g. attribute metadata."""
    await ih.TestingDevice.create(
        name={"value": "sw-wire", "is_protected": True}, site={"id": SITE_ID}
    )
    assert "is_protected: true" in last_query(fake)


async def test_create_accepts_a_positional_dict(ih):
    device = await ih.TestingDevice.create({"name": "sw-dict"})
    assert device.name == "sw-dict"


async def test_upsert_updates_an_existing_object(ih, fake):
    device = await ih.TestingDevice.upsert(name="sw-1", serial="UPSERTED")
    assert device.id == DEVICE_IDS[0]
    assert device.serial == "UPSERTED"
    assert len(fake.objects["TestingDevice"]) == 5


async def test_upsert_creates_a_missing_object(ih, fake):
    device = await ih.TestingDevice.upsert(name="sw-6")
    assert device.name == "sw-6"
    assert len(fake.objects["TestingDevice"]) == 6


async def test_include_adds_a_non_default_relationship(ih):
    """Component many-rels stay out of the default selection."""
    device = await ih.TestingDevice.get("sw-1", include=["interfaces"])
    assert device is not None
    assert [str(i) for i in device.interfaces] == ["Ethernet1"]


async def test_default_selection_omits_component_relationships(ih):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    with pytest.raises(AttributeError):
        device.interfaces  # noqa: B018


async def test_exclude_drops_a_field(ih):
    device = await ih.TestingDevice.get("sw-1", exclude=["serial"])
    assert device is not None
    with pytest.raises(AttributeError):
        device.serial  # noqa: B018


async def test_properties_selects_metadata(ih, fake):
    await ih.TestingDevice.get("sw-1", properties=True)
    query = last_query(fake)
    assert "is_protected" in query
    assert "properties {" in query


async def test_properties_is_off_by_default(ih, fake):
    await ih.TestingDevice.get("sw-1")
    assert "is_protected" not in last_query(fake)


async def test_properties_on_all_and_filter(ih, fake):
    devices = [d async for d in ih.TestingDevice.all(properties=True)]
    assert devices[0].meta("name").is_protected is True
    devices = [
        d async for d in ih.TestingDevice.filter(name__value="sw-1", properties=True)
    ]
    assert devices[0].meta("name").is_protected is True


async def test_branch_is_a_url_path_segment(ih, fake):
    devices = [d async for d in ih.TestingDevice.all(branch="feature-x")]
    assert len(devices) == 5
    assert fake.requests[-1].url.path == "/graphql/feature-x"


async def test_client_branch_applies_without_a_per_call_override(fake):
    async with make_api(fake, branch="feature-x") as ih:
        await ih.TestingDevice.get("sw-1")
    assert fake.requests[-1].url.path == "/graphql/feature-x"


async def test_at_becomes_a_query_param_on_reads(ih, fake):
    await ih.TestingDevice.get("sw-1", at="2026-08-01T00:00:00Z")
    assert fake.requests[-1].url.params["at"] == "2026-08-01T00:00:00Z"


async def test_mutations_target_a_branch(ih, fake):
    """Mutations take a branch but no `at`; the server resets it to now."""
    await ih.TestingDevice.create(name="sw-branch", branch="feature-x")
    assert fake.requests[-1].url.path == "/graphql/feature-x"
