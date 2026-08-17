import json

import httpx
import pytest
from conftest import BASE, DEVICE_IDS, SITE_ID, make_api, parse_query

import aiopyinfrahub
from aiopyinfrahub.response import Record


def last_query(fake):
    return json.loads(fake.requests[-1].content)["query"]


async def test_token_uses_the_infrahub_key_header(ih, fake):
    """Infrahub's API token header, not `Authorization: Token`."""
    await ih.version()
    assert fake.requests[-1].headers["X-INFRAHUB-KEY"] == "abc123"


async def test_no_token_sends_no_auth_header(fake):
    """Infrahub allows anonymous reads, so a tokenless client is legal."""
    async with make_api(fake, token=None) as ih:
        await ih.version()
    assert "X-INFRAHUB-KEY" not in fake.requests[-1].headers


async def test_user_agent_identifies_library(ih, fake):
    await ih.version()
    assert fake.requests[-1].headers["User-Agent"].startswith("python-aiopyinfrahub/")


async def test_accept_header(ih, fake):
    await ih.version()
    assert fake.requests[-1].headers["Accept"] == "application/json"


def test_base_url_keeps_no_prefix(fake):
    """GraphQL lives at /graphql and REST at /api, so neither is baked in."""
    api = aiopyinfrahub.api(f"{BASE}/", token="x")
    assert api.base_url == BASE


async def test_version_reads_api_info(ih, fake):
    assert await ih.version() == "1.10.8"
    assert fake.requests[-1].url.path == "/api/info"


async def test_schema_is_fetched_once_for_two_operations(ih, fake):
    await ih.TestingDevice.get(DEVICE_IDS[0])
    await ih.TestingDevice.count()
    calls = [r for r in fake.requests if r.url.path == "/api/schema"]
    assert len(calls) == 1


async def test_schema_is_cached_per_branch(ih, fake):
    """The schema is per-branch and mutable, so branches cannot share one."""
    await ih.schema()
    await ih.schema(branch="feature-x")
    await ih.schema(branch="feature-x")
    calls = [r for r in fake.requests if r.url.path == "/api/schema"]
    assert len(calls) == 2
    assert calls[-1].url.params["branch"] == "feature-x"


async def test_schema_refresh_refetches(ih, fake):
    await ih.schema()
    await ih.schema(refresh=True)
    calls = [r for r in fake.requests if r.url.path == "/api/schema"]
    assert len(calls) == 2


async def test_schema_is_flattened_by_kind(ih):
    schema = await ih.schema()
    assert schema["TestingDevice"]["default_filter"] == "name__value"


async def test_client_branch_is_the_default_schema_key(fake):
    async with make_api(fake, branch="feature-x") as ih:
        await ih.schema()
    assert fake.requests[-1].url.params["branch"] == "feature-x"


async def test_search_yields_hits(ih):
    hits = [hit async for hit in ih.search("sw-1")]
    assert [hit.id for hit in hits] == [DEVICE_IDS[0]]
    assert hits[0].kind == "TestingDevice"


async def test_search_is_lazy(ih, fake):
    ih.search("sw")
    assert not fake.requests


async def test_search_hits_are_brief(ih):
    """InfrahubSearchAnywhere answers with id and kind and nothing else."""
    hits = [hit async for hit in ih.search("sw-1")]
    with pytest.raises(AttributeError, match="full_details"):
        hits[0].name  # noqa: B018
    assert await hits[0].full_details() is True
    assert hits[0].name == "sw-1"


async def test_search_limit(ih, fake):
    hits = [hit async for hit in ih.search("sw", limit=2)]
    assert len(hits) == 2
    assert "limit: 2" in last_query(fake)


async def test_search_omits_unset_arguments(ih, fake):
    await anext(aiter(ih.search("sw-1")))
    assert "partial_match" not in last_query(fake)


async def test_convert_object_type_returns_the_new_node(ih, fake):
    node = await ih.convert_object_type(
        DEVICE_IDS[1], "TestingInterface", {"name": {"source_field": "name"}}
    )
    assert isinstance(node, Record)
    assert node.id == DEVICE_IDS[1]
    assert fake.objects["TestingInterface"][DEVICE_IDS[1]]["name"] == "sw-2"
    _, name, args, _ = parse_query(last_query(fake))
    assert name == "ConvertObjectType"
    assert args["data"] == {
        "node_id": DEVICE_IDS[1],
        "target_kind": "TestingInterface",
        "fields_mapping": {"name": {"source_field": "name"}},
    }


async def test_convert_object_type_of_an_unknown_node_raises(ih):
    with pytest.raises(aiopyinfrahub.GraphQLError, match="not found"):
        await ih.convert_object_type(SITE_ID.replace("2", "8"), "TestingSite", {})


async def test_context_manager_closes_owned_client():
    api = aiopyinfrahub.api(BASE, token="x")
    async with api:
        pass
    assert api._client.is_closed


async def test_supplied_client_is_not_closed(fake):
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    async with aiopyinfrahub.api(BASE, token="x", client=client) as ih:
        await ih.version()
    assert not client.is_closed
    await client.aclose()


async def test_request_error_carries_status_and_body(ih, fake):
    fake.fail_next = [400]
    with pytest.raises(aiopyinfrahub.RequestError) as excinfo:
        await ih.version()
    assert excinfo.value.status_code == 400
    assert "injected" in excinfo.value.error


async def test_content_error_on_non_json(fake):
    fake.handler = lambda request: httpx.Response(200, text="<html>nope</html>")
    async with make_api(fake) as bad:
        with pytest.raises(aiopyinfrahub.ContentError, match="not an Infrahub server"):
            await bad.version()


async def test_unhandled_route_raises_request_error(ih, fake):
    """The fake answers anything it does not model with a loud 500."""
    with pytest.raises(aiopyinfrahub.RequestError):
        await ih._request("GET", f"{BASE}/api/nope")
