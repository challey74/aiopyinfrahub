import json

import pytest
from conftest import DEVICE_IDS, INTERFACE_ID, SITE_ID, TAG_IDS, make_api, parse_query

import aiopyinfrahub


def last_query(fake):
    return json.loads(fake.requests[-1].content)["query"]


async def test_paths_between_two_nodes(ih):
    result = await ih.graph.paths(DEVICE_IDS[0], SITE_ID)
    assert result["count"] == 1
    assert result["source"]["display_label"] == "sw-1"
    assert result["destination"]["kind"] == "TestingSite"
    path = result["paths"][0]
    assert path["depth"] == 1
    assert [hop["node"]["id"] for hop in path["hops"]] == [DEVICE_IDS[0], SITE_ID]
    # The first hop is the source itself, so it arrived on nothing.
    assert path["hops"][0]["relationship"] is None
    assert path["hops"][1]["relationship"]["from_rel"] == "site"


async def test_paths_walks_more_than_one_hop(ih):
    result = await ih.graph.paths(SITE_ID, INTERFACE_ID)
    path = result["paths"][0]
    assert path["depth"] == 2
    assert [hop["node"]["id"] for hop in path["hops"]] == [
        SITE_ID,
        DEVICE_IDS[0],
        INTERFACE_ID,
    ]


async def test_paths_finds_nothing_when_nothing_connects(ih):
    result = await ih.graph.paths(DEVICE_IDS[1], SITE_ID)
    assert result["count"] == 0
    assert result["paths"] == []


async def test_paths_sends_a_data_input(ih, fake):
    await ih.graph.paths(
        DEVICE_IDS[0],
        SITE_ID,
        max_depth=3,
        max_paths=2,
        shortest_paths_only=False,
        kind_filter=["TestingSite"],
        relationship_filter=["device__site"],
    )
    _, name, args, _ = parse_query(last_query(fake))
    assert name == "InfrahubPathTraversal"
    assert args["data"] == {
        "source_id": DEVICE_IDS[0],
        "destination_id": SITE_ID,
        "max_depth": 3,
        "max_paths": 2,
        "shortest_paths_only": False,
        "kind_filter": ["TestingSite"],
        "relationship_filter": ["device__site"],
    }


async def test_paths_omits_unset_arguments(ih, fake):
    await ih.graph.paths(DEVICE_IDS[0], SITE_ID)
    _, _, args, _ = parse_query(last_query(fake))
    assert args["data"] == {
        "source_id": DEVICE_IDS[0],
        "destination_id": SITE_ID,
    }


async def test_path_exists(ih):
    assert await ih.graph.path_exists(DEVICE_IDS[0], SITE_ID) is True
    assert await ih.graph.path_exists(DEVICE_IDS[1], SITE_ID) is False


async def test_path_exists_asks_for_one_path_and_the_count_only(ih, fake):
    await ih.graph.path_exists(DEVICE_IDS[0], SITE_ID)
    query = last_query(fake)
    assert "max_paths: 1" in query
    assert "hops" not in query


async def test_an_unknown_endpoint_raises(ih):
    with pytest.raises(aiopyinfrahub.GraphQLError, match="must exist"):
        await ih.graph.paths(DEVICE_IDS[0], "no-such-node")


async def test_reachable_nodes_of_one_kind(ih):
    result = await ih.graph.reachable_nodes(SITE_ID, ["BuiltinTag"])
    assert result["count"] == 2
    assert result["source"]["id"] == SITE_ID
    assert {dep["node"]["id"] for dep in result["dependencies"]} == set(TAG_IDS)
    dependency = result["dependencies"][0]
    assert dependency["depth"] == 2
    assert dependency["path"]["hops"][0]["node"]["id"] == SITE_ID


async def test_reachable_nodes_excludes_the_source(ih):
    result = await ih.graph.reachable_nodes(SITE_ID, ["TestingSite"])
    assert result["count"] == 0


async def test_reachable_nodes_honors_max_results(ih):
    result = await ih.graph.reachable_nodes(SITE_ID, ["BuiltinTag"], max_results=1)
    assert result["count"] == 1
    assert len(result["dependencies"]) == 1


async def test_reachable_nodes_sends_its_input(ih, fake):
    await ih.graph.reachable_nodes(
        SITE_ID,
        ["BuiltinTag"],
        max_depth=2,
        max_paths=10,
        max_results=1,
        shortest_paths_only=True,
    )
    _, name, args, _ = parse_query(last_query(fake))
    assert name == "InfrahubReachableNodes"
    assert args["data"] == {
        "source_id": SITE_ID,
        "target_kinds": ["BuiltinTag"],
        "max_depth": 2,
        "max_paths": 10,
        "max_results": 1,
        "shortest_paths_only": True,
    }


async def test_traversal_runs_against_a_branch(fake):
    async with make_api(fake) as ih:
        await ih.graph.paths(DEVICE_IDS[0], SITE_ID, branch="feature-x")
    assert fake.requests[-1].url.path == "/graphql/feature-x"
