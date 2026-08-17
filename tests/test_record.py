import json

import pytest
from conftest import (
    DEVICE_IDS,
    SITE_ID,
    TAG_IDS,
    FakeInfrahub,
    make_api,
    make_device,
    parse_query,
)

from aiopyinfrahub.response import Record


def mutations(fake):
    return [
        parse_query(json.loads(r.content)["query"])
        for r in fake.requests
        if r.url.path.startswith("/graphql")
        and json.loads(r.content)["query"].startswith("mutation")
    ]


async def test_attribute_wrappers_are_flattened(ih):
    """`{"value": v}` becomes the bare value, so device.name == "sw-1"."""
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    assert device.name == "sw-1"
    assert device.serial == "ABC123"
    assert device.port_count == 48


async def test_cardinality_one_relationship_becomes_a_record(ih):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    assert isinstance(device.site, Record)
    assert device.site.display_label == "atl1"


async def test_cardinality_many_relationship_becomes_a_list(ih):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    assert [str(t) for t in device.tags] == ["prod", "edge"]


async def test_empty_relationships(ih):
    device = await ih.TestingDevice.get("sw-2")
    assert device is not None
    assert device.site is None
    assert device.tags == []


async def test_brief_peer_attribute_raises_with_guidance(ih):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    with pytest.raises(AttributeError, match="full_details"):
        device.site.name  # noqa: B018


async def test_full_details_loads_a_brief_peer(ih):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    assert await device.site.full_details() is True
    assert device.site.name == "atl1"


async def test_full_details_without_identity_returns_false(ih):
    assert await Record({"name": "loose"}, ih).full_details() is False


async def test_save_sends_only_the_diff(ih, fake):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    device.serial = "XYZ789"
    assert await device.save() is True
    _, name, args, _ = mutations(fake)[-1]
    assert name == "TestingDeviceUpdate"
    assert args["data"] == {"id": DEVICE_IDS[0], "serial": {"value": "XYZ789"}}


async def test_save_rewraps_relationships(ih, fake):
    device = await ih.TestingDevice.get("sw-2")
    assert device is not None
    device.site = SITE_ID
    device.tags = [TAG_IDS[0]]
    assert await device.save() is True
    _, _, args, _ = mutations(fake)[-1]
    assert args["data"]["site"] == {"id": SITE_ID}
    assert args["data"]["tags"] == [{"id": TAG_IDS[0]}]


async def test_save_reparses_the_response(ih):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    device.serial = "REPARSED"
    await device.save()
    assert device.serial == "REPARSED"
    assert await device.save() is False


async def test_save_without_changes_sends_nothing(ih, fake):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    before = len(fake.requests)
    assert await device.save() is False
    assert len(fake.requests) == before


async def test_updates_reports_the_diff(ih):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    device.serial = "NEW"
    assert device.updates() == {"serial": "NEW"}


async def test_update_sets_then_saves(ih, fake):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    assert await device.update({"serial": "FROM-UPDATE"}) is True
    _, _, args, _ = mutations(fake)[-1]
    assert args["data"]["serial"] == {"value": "FROM-UPDATE"}


async def test_delete(ih, fake):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    assert await device.delete() is True
    _, name, args, _ = mutations(fake)[-1]
    assert name == "TestingDeviceDelete"
    assert args["data"] == {"id": DEVICE_IDS[0]}
    assert DEVICE_IDS[0] not in fake.objects["TestingDevice"]


async def test_record_without_identity_cannot_be_saved(ih):
    record = Record({"name": "loose"}, ih)
    record.name = "changed"
    with pytest.raises(ValueError, match="no id and __typename"):
        await record.save()


async def test_equality_by_branch_and_id(ih):
    a = await ih.TestingDevice.get(DEVICE_IDS[0])
    b = await ih.TestingDevice.get(DEVICE_IDS[0])
    c = await ih.TestingDevice.get(DEVICE_IDS[1])
    assert a == b and hash(a) == hash(b)
    assert a != c
    assert len({a, b, c}) == 2


async def test_the_same_id_on_two_branches_is_two_records(ih):
    """Ids are unique per branch, and two branches are two states."""
    a = await ih.TestingDevice.get(DEVICE_IDS[0])
    b = await ih.TestingDevice.get(DEVICE_IDS[0], branch="feature-x")
    assert a != b


async def test_records_without_an_id_compare_by_identity(ih):
    assert Record({"name": "x"}, ih) != Record({"name": "x"}, ih)


async def test_dict_cast_and_getitem(ih):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    as_dict = dict(device)
    assert as_dict["name"] == "sw-1"
    assert as_dict["site"]["display_label"] == "atl1"
    assert device["serial"] == "ABC123"


async def test_str_prefers_display_label(ih):
    device = await ih.TestingDevice.get("sw-1")
    assert str(device) == "sw-1"
    assert repr(device) == "<Record (sw-1)>"


async def test_serialize_collapses_peers_to_ids(ih):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    data = device.serialize()
    assert data["site"] == SITE_ID
    assert data["tags"] == TAG_IDS
    # __typename starts with an underscore, so it never reaches a mutation.
    assert "__typename" not in data


async def test_json_attribute_value_stays_raw(ih):
    """A JSON-kind attribute holds dicts, which serialize must not collapse."""
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    assert device.config == {"ntp": ["10.0.0.1"]}
    assert device.serialize()["config"] == {"ntp": ["10.0.0.1"]}
    assert device.updates() == {}


async def test_json_attribute_is_rewrapped_on_save(ih, fake):
    """Its value is a dict, so only the parse-time memory can wrap it."""
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    device.config = {"ntp": ["10.0.0.2"]}
    assert await device.save() is True
    _, _, args, _ = mutations(fake)[-1]
    assert args["data"]["config"] == {"value": {"ntp": ["10.0.0.2"]}}


async def test_properties_keeps_reads_flattened(ih):
    """Metadata rides alongside; device.name is still the bare value."""
    device = await ih.TestingDevice.get("sw-1", properties=True)
    assert device is not None
    assert device.name == "sw-1"
    assert device.site.display_label == "atl1"


async def test_meta_hands_back_attribute_metadata(ih):
    device = await ih.TestingDevice.get("sw-1", properties=True)
    assert device is not None
    meta = device.meta("name")
    assert meta.is_protected is True
    assert meta.is_default is False
    assert meta.updated_at == "2026-08-17T00:00:00Z"
    # source and owner name a lineage peer rather than being one.
    assert meta.source["display_label"] == "netbox"
    assert meta.owner["display_label"] == "otto"


async def test_meta_hands_back_relationship_properties(ih):
    device = await ih.TestingDevice.get("sw-1", properties=True)
    assert device is not None
    assert device.meta("site").is_protected is False
    # Infrahub hangs a many-relationship's properties off each edge, so
    # the metadata is a list positioned against the peers.
    assert [str(t) for t in device.tags] == ["prod", "edge"]
    assert [m.updated_at for m in device.meta("tags")] == [
        "2026-08-17T00:00:00Z",
        "2026-08-17T00:00:00Z",
    ]


async def test_meta_without_properties_raises(ih):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    with pytest.raises(ValueError, match="properties=True"):
        device.meta("name")


async def test_meta_is_never_serialized_or_diffed(ih):
    device = await ih.TestingDevice.get("sw-1", properties=True)
    assert device is not None
    assert device.serialize()["name"] == "sw-1"
    assert "is_protected" not in json.dumps(device.serialize())
    assert device.updates() == {}
    assert "_meta" not in dict(device)


async def test_meta_is_never_saved(ih, fake):
    device = await ih.TestingDevice.get("sw-1", properties=True)
    assert device is not None
    device.serial = "XYZ789"
    assert await device.save() is True
    _, _, args, _ = mutations(fake)[-1]
    assert args["data"] == {"id": DEVICE_IDS[0], "serial": {"value": "XYZ789"}}


async def test_fetch_hydrates_a_component_relationship(ih):
    """Component many-rels stay out of the default selection."""
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    with pytest.raises(AttributeError):
        device.interfaces  # noqa: B018
    interfaces = await device.fetch("interfaces")
    assert [str(i) for i in interfaces] == ["Ethernet1"]
    assert device.interfaces == interfaces


async def test_fetch_returns_a_cardinality_one_peer(ih):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    site = await device.fetch("site")
    assert isinstance(site, Record)
    assert str(site) == "atl1"


async def test_fetch_leaves_the_record_clean(ih):
    """The merged relationship is snapshotted, so it is not a pending edit."""
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    await device.fetch("interfaces")
    assert device.updates() == {}


async def test_fetch_keeps_a_pending_edit(ih):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    device.serial = "PENDING"
    await device.fetch("interfaces")
    assert device.updates() == {"serial": "PENDING"}


async def test_fetch_of_an_unknown_relationship_raises(ih):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    with pytest.raises(ValueError, match="Did you mean: interfaces"):
        await device.fetch("interface")


async def test_fetch_without_identity_raises(ih):
    with pytest.raises(ValueError, match="no id and __typename"):
        await Record({"name": "loose"}, ih).fetch("site")


async def test_add_related(ih, fake):
    device = await ih.TestingDevice.get("sw-2")
    assert device is not None
    assert await device.add_related("tags", TAG_IDS[0]) is True
    _, name, args, _ = mutations(fake)[-1]
    assert name == "RelationshipAdd"
    assert args["data"] == {
        "id": DEVICE_IDS[1],
        "name": "tags",
        "nodes": [{"id": TAG_IDS[0]}],
    }
    assert fake.objects["TestingDevice"][DEVICE_IDS[1]]["tags"] == [TAG_IDS[0]]


async def test_add_related_normalizes_peers(ih, fake):
    """A str id, a Record, and a RelatedNodeInput dict are all accepted."""
    device = await ih.TestingDevice.get("sw-2")
    assert device is not None
    tag = await ih.BuiltinTag.get("prod")
    await device.add_related("tags", [tag, {"hfid": ["edge"]}])
    _, _, args, _ = mutations(fake)[-1]
    assert args["data"]["nodes"] == [{"id": TAG_IDS[0]}, {"hfid": ["edge"]}]
    assert fake.objects["TestingDevice"][DEVICE_IDS[1]]["tags"] == TAG_IDS


async def test_remove_related(ih, fake):
    device = await ih.TestingDevice.get("sw-1")
    assert device is not None
    assert await device.remove_related("tags", TAG_IDS[0]) is True
    _, name, _, _ = mutations(fake)[-1]
    assert name == "RelationshipRemove"
    assert fake.objects["TestingDevice"][DEVICE_IDS[0]]["tags"] == [TAG_IDS[1]]


async def test_related_mutations_need_an_identity(ih):
    with pytest.raises(ValueError, match="no id and __typename"):
        await Record({"name": "loose"}, ih).add_related("tags", "x")


async def test_filter_iterates_lazily(ih, fake):
    recordset = ih.TestingDevice.filter(name__value="sw-1")
    assert not fake.requests  # nothing fetched until iteration
    names = [d.name async for d in recordset]
    assert names == ["sw-1"]


async def test_pagination_fans_out_and_preserves_order(fake):
    async with make_api(fake, page_size=2) as ih:
        names = [d.name async for d in ih.TestingDevice.all()]
    assert names == ["sw-1", "sw-2", "sw-3", "sw-4", "sw-5"]


async def test_explicit_offset_fetches_one_page(ih):
    names = [d.name async for d in ih.TestingDevice.all(limit=2, offset=2)]
    assert names == ["sw-3", "sw-4"]


async def test_offset_passed_as_a_filter_pins_the_page(ih):
    """An offset in the filters pins the query just like the all() arg."""
    names = [d.name async for d in ih.TestingDevice.filter(offset=2, limit=2)]
    assert names == ["sw-3", "sw-4"]


async def test_server_capped_page_size_skips_nothing(fake):
    """Offset arithmetic must trust the served page size, not the requested
    one: a server that caps `limit` would otherwise leave gaps."""
    fake.max_limit = 2
    async with make_api(fake) as ih:  # asks for 50 per page, gets 2
        names = [d.name async for d in ih.TestingDevice.all()]
    assert names == ["sw-1", "sw-2", "sw-3", "sw-4", "sw-5"]


async def test_early_break_does_not_fetch_every_page():
    """The fan-out is a sliding window, so abandoning iteration stops it."""
    fake = FakeInfrahub(
        devices=[
            make_device(f"{i:08d}-0000-4000-8000-000000000000", f"sw-{i:02d}")
            for i in range(40)
        ]
    )
    async with make_api(fake, page_size=2) as ih:
        seen = []
        async for device in ih.TestingDevice.all():
            seen.append(device.name)
            if len(seen) == 3:  # one record past the first page
                break
        assert seen == ["sw-00", "sw-01", "sw-02"]
        assert len(fake.requests) <= 2 + ih.max_concurrency + 2


async def test_recordset_reruns_each_iteration(ih, fake):
    recordset = ih.TestingDevice.all()
    assert [d.name async for d in recordset]
    first = len(fake.requests)
    assert [d.name async for d in recordset]
    assert len(fake.requests) > first


async def test_recordset_count(ih):
    assert await ih.TestingDevice.filter(name__value="sw-1").count() == 1
