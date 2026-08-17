import pytest
from conftest import SCHEMA, SITE_ID, TAG_IDS

from aiopyinfrahub.schema import (
    BRIEF_FIELDS,
    LINEAGE_FIELDS,
    REL_PROPERTIES,
    build_input,
    build_selection,
    flatten,
    kind_name,
    lookup,
)

KINDS = flatten(SCHEMA)
DEVICE = KINDS["TestingDevice"]


def test_kind_name_joins_namespace_and_name():
    assert kind_name({"namespace": "Infra", "name": "Device"}) == "InfraDevice"


def test_attribute_namespace_is_elided():
    """The server names Attribute-namespace kinds by the bare name."""
    assert kind_name({"namespace": "Attribute", "name": "Text"}) == "Text"


def test_flatten_covers_every_section():
    assert "TestingDevice" in KINDS  # nodes
    assert "CoreNode" in KINDS  # generics
    assert "ProfileTestingDevice" in KINDS  # profiles
    assert "TemplateTestingDevice" in KINDS  # templates


def test_lookup_suggests_close_matches():
    with pytest.raises(ValueError, match="Did you mean: TestingDevice"):
        lookup(KINDS, "TestingDevic")


def test_lookup_without_a_close_match():
    with pytest.raises(ValueError, match="not a kind in this branch's schema"):
        lookup(KINDS, "Zzzzzzzz")


def test_default_selection_shape():
    selection = build_selection(DEVICE)
    assert selection["id"] is None
    assert selection["__typename"] is None
    assert selection["name"] == {"value": None}
    assert selection["site"] == {"node": BRIEF_FIELDS}
    assert selection["tags"] == {"count": None, "edges": {"node": BRIEF_FIELDS}}


def test_default_selection_omits_component_relationships():
    """Fetching them by default makes every list query fan out."""
    assert "interfaces" not in build_selection(DEVICE)


def test_include_adds_a_non_default_relationship():
    assert "interfaces" in build_selection(DEVICE, include=["interfaces"])


def test_exclude_drops_attributes_and_relationships():
    selection = build_selection(DEVICE, exclude=["serial", "site", "tags"])
    assert "serial" not in selection
    assert "site" not in selection
    assert "tags" not in selection


def test_properties_selection_adds_attribute_metadata():
    selection = build_selection(DEVICE, properties=True)
    assert selection["name"]["value"] is None
    assert selection["name"]["is_protected"] is None
    assert selection["name"]["is_default"] is None
    assert selection["name"]["source"] == LINEAGE_FIELDS


def test_properties_selection_adds_relationship_properties():
    """Cardinality-one carries them beside node; many, inside each edge."""
    selection = build_selection(DEVICE, properties=True)
    assert selection["site"]["properties"] == REL_PROPERTIES
    assert selection["tags"]["edges"]["properties"] == REL_PROPERTIES


def test_relationship_properties_have_no_is_default():
    """RelationshipProperty carries four fields; is_default is
    attribute-only."""
    assert set(REL_PROPERTIES) == {"is_protected", "updated_at", "source", "owner"}


def test_no_selection_mentions_is_visible():
    """It was removed from Infrahub; is_protected is the only flag left."""
    assert "is_visible" not in str(build_selection(DEVICE, properties=True))


def test_default_selection_has_no_metadata():
    selection = build_selection(DEVICE)
    assert selection["name"] == {"value": None}
    assert "properties" not in selection["site"]


def test_build_input_wraps_by_schema():
    data = build_input(DEVICE, {"name": "sw-1", "site": SITE_ID, "tags": [TAG_IDS[0]]})
    assert data == {
        "name": {"value": "sw-1"},
        "site": {"id": SITE_ID},
        "tags": [{"id": TAG_IDS[0]}],
    }


def test_build_input_passes_the_wire_shape_through():
    data = build_input(
        DEVICE,
        {
            "name": {"value": "x", "is_protected": True},
            "site": {"hfid": ["atl1"]},
            "tags": [{"hfid": ["prod"]}],
        },
    )
    assert data["name"]["is_protected"] is True
    assert data["site"] == {"hfid": ["atl1"]}
    assert data["tags"] == [{"hfid": ["prod"]}]


def test_build_input_clears_a_relationship_with_none():
    assert build_input(DEVICE, {"site": None}) == {"site": None}


def test_build_input_leaves_unknown_keys_alone():
    """`id` and `hfid` are mutation keys, not schema fields."""
    assert build_input(DEVICE, {"id": "x"}) == {"id": "x"}
