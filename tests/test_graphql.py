import json

import pytest
from conftest import make_api

import aiopyinfrahub
from aiopyinfrahub.graphql import (
    EnumValue,
    render_args,
    render_mutation,
    render_query,
    render_value,
)

QUERY = "query { Branch { name } }"


async def test_query_returns_data(ih):
    result = await ih.graphql.query(QUERY)
    assert result.status_code == 200
    assert result.data["Branch"][0]["name"] == "main"
    assert result.errors == []


async def test_query_posts_to_the_graphql_root(ih, fake):
    await ih.graphql.query(QUERY, variables={"name": "sw-1"})
    request = fake.requests[-1]
    assert request.url.path == "/graphql"
    assert json.loads(request.content) == {
        "query": QUERY,
        "variables": {"name": "sw-1"},
    }


async def test_branch_is_a_percent_encoded_path_segment(fake):
    """Infrahub has no `branch` query param on /graphql; it is path-only."""
    async with make_api(fake, branch="feature/x") as ih:
        await ih.graphql.query(QUERY)
    assert "feature%2Fx" in str(fake.requests[-1].url)


async def test_per_call_branch_overrides_the_client_default(fake):
    async with make_api(fake, branch="main") as ih:
        await ih.graphql.query(QUERY, branch="feature-x")
    assert fake.requests[-1].url.path == "/graphql/feature-x"


async def test_at_becomes_a_query_param(ih, fake):
    await ih.graphql.query(QUERY, at="2026-08-01T00:00:00Z")
    assert fake.requests[-1].url.params["at"] == "2026-08-01T00:00:00Z"


async def test_200_with_errors_does_not_raise_on_the_raw_path(ih, fake):
    """Partial data is often still useful; only the kind layer raises."""
    fake.canned["partial"] = {
        "data": {"partial": None},
        "errors": [{"message": "nope"}],
    }
    result = await ih.graphql.query("query { partial }")
    assert result.errors[0]["message"] == "nope"
    assert result.data == {"partial": None}


async def test_400_with_errors_raises_graphql_error(ih, fake):
    fake.fail_next = [400]
    with pytest.raises(aiopyinfrahub.GraphQLError) as excinfo:
        await ih.graphql.query(QUERY)
    assert excinfo.value.status_code == 400
    assert excinfo.value.errors[0]["message"] == "injected"


async def test_non_400_failure_stays_a_request_error(ih, fake):
    fake.fail_next = [401]
    with pytest.raises(aiopyinfrahub.RequestError):
        await ih.graphql.query(QUERY)


async def test_kind_layer_raises_on_200_with_errors(ih, fake):
    """Infrahub reports execution failures with HTTP 200 plus errors."""
    fake.canned["TestingDevice("] = {
        "data": None,
        "errors": [{"message": "boom"}],
    }
    with pytest.raises(aiopyinfrahub.GraphQLError, match="boom"):
        await ih.TestingDevice.get("sw-1")


async def test_query_type_is_checked(ih):
    with pytest.raises(TypeError, match="query must be a str"):
        await ih.graphql.query({"not": "a string"})


async def test_variables_type_is_checked(ih):
    with pytest.raises(TypeError, match="variables must be a dict"):
        await ih.graphql.query(QUERY, variables=["nope"])


async def test_record_repr_hides_body(ih):
    result = await ih.graphql.query(QUERY)
    assert repr(result) == "GraphQLRecord(status_code=200)"
    assert "Branch" in str(result)


async def test_stored_runs_the_saved_query(ih, fake):
    result = await ih.graphql.stored("device-names")
    assert result.status_code == 200
    assert result.data["TestingDevice"]["count"] == 5
    assert fake.requests[-1].url.path == "/api/query/device-names"


async def test_stored_posts_its_variables(ih, fake):
    await ih.graphql.stored("device-names", variables={"name": "sw-1"})
    assert json.loads(fake.requests[-1].content) == {"variables": {"name": "sw-1"}}
    assert fake.stored_calls[-1]["variables"] == {"name": "sw-1"}


async def test_stored_without_variables_still_sends_the_key(ih, fake):
    await ih.graphql.stored("device-names")
    assert json.loads(fake.requests[-1].content) == {"variables": {}}


async def test_stored_repeats_subscribers(ih, fake):
    await ih.graphql.stored("device-names", update_group=True, subscribers=["a", "b"])
    call = fake.stored_calls[-1]
    assert call["subscribers"] == ["a", "b"]
    assert call["update_group"] == "true"


async def test_stored_takes_the_branch_as_a_query_param(fake):
    """/api/query is a REST route, so the branch is not a path suffix."""
    async with make_api(fake, branch="feature-x") as ih:
        await ih.graphql.stored("device-names", at="2026-08-01T00:00:00Z")
    request = fake.requests[-1]
    assert request.url.path == "/api/query/device-names"
    assert request.url.params["branch"] == "feature-x"
    assert request.url.params["at"] == "2026-08-01T00:00:00Z"


async def test_stored_omits_unset_parameters(ih, fake):
    await ih.graphql.stored("device-names")
    assert not dict(fake.requests[-1].url.params)


async def test_stored_percent_encodes_the_id(ih, fake):
    """The id may be a name, which is caller data in a path segment."""
    with pytest.raises(aiopyinfrahub.RequestError):
        await ih.graphql.stored("my query/v2")
    assert "/api/query/my%20query%2Fv2" in str(fake.requests[-1].url)


async def test_unknown_stored_query_raises(ih):
    with pytest.raises(aiopyinfrahub.RequestError) as excinfo:
        await ih.graphql.stored("no-such-query")
    assert excinfo.value.status_code == 404


def test_render_query_shape():
    query = render_query(
        {"count": None, "edges": {"node": {"id": None}}},
        kind="InfraDevice",
        filters={"limit": 2},
    )
    assert query == (
        "query {\n"
        "    InfraDevice(limit: 2) {\n"
        "        count\n"
        "        edges {\n"
        "            node {\n"
        "                id\n"
        "            }\n"
        "        }\n"
        "    }\n"
        "}"
    )


def test_render_mutation_shape():
    mutation = render_mutation(
        {"ok": None},
        name="BranchCreate",
        data={"name": "feature-x"},
        extra_args={"wait_until_completion": True},
    )
    assert mutation == (
        "mutation {\n"
        '    BranchCreate(data: {name: "feature-x"}, wait_until_completion: true) {\n'
        "        ok\n"
        "    }\n"
        "}"
    )


def test_render_value_literals():
    assert render_value(None) == "null"
    assert render_value(True) == "true"
    assert render_value(False) == "false"
    assert render_value(7) == "7"
    assert render_value(["a", 1]) == '["a", 1]'
    assert render_value({"id": "x"}) == '{id: "x"}'


def test_enum_values_render_bare():
    """graphene rejects a quoted literal where an enum is declared, which
    is how InfrahubTask's `state:` is typed."""
    assert render_value(EnumValue("RUNNING")) == "RUNNING"
    assert render_value("RUNNING") == '"RUNNING"'
    assert render_value([EnumValue("RUNNING")]) == "[RUNNING]"


def test_enum_values_are_still_identifier_checked():
    with pytest.raises(ValueError, match="enum value"):
        render_value(EnumValue('RUNNING"] } evil {'))


def test_render_value_escapes_strings():
    """A quote in a value must not be able to close the literal."""
    assert render_value('a" b') == '"a\\" b"'


def test_render_args_of_nothing_is_empty():
    assert render_args({}) == ""


def test_bad_kind_is_rejected():
    with pytest.raises(ValueError, match="kind .* not a valid GraphQL identifier"):
        render_query({"id": None}, kind="Infra Device")


def test_bad_filter_key_is_rejected():
    with pytest.raises(ValueError, match="argument"):
        render_query({"id": None}, kind="InfraDevice", filters={"a) { id } x(b": 1})


def test_bad_field_name_is_rejected():
    with pytest.raises(ValueError, match="field"):
        render_query({"id } evil {": None}, kind="InfraDevice")


def test_bad_input_key_is_rejected():
    with pytest.raises(ValueError, match="input key"):
        render_mutation({"ok": None}, name="XCreate", data={"a b": 1})


def test_bad_mutation_name_is_rejected():
    with pytest.raises(ValueError, match="mutation"):
        render_mutation({"ok": None}, name="X Create", data={})
