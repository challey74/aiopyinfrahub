import json

from conftest import ARTIFACT_ID, DEVICE_IDS, parse_query


def last_query(fake):
    return json.loads(fake.requests[-1].content)["query"]


async def test_summary_is_counts_only(ih):
    summary = await ih.diff.summary("feature-x")
    assert summary is not None
    assert summary["diff_branch"] == "feature-x"
    assert summary["base_branch"] == "main"
    assert summary["num_added"] == 1
    assert summary["num_unchanged"] == 3
    assert "nodes" not in summary


async def test_summary_without_a_diff_is_none(ih):
    assert await ih.diff.summary("main") is None


async def test_tree_carries_nodes_and_their_attributes(ih):
    tree = await ih.diff.tree("feature-x")
    assert tree is not None
    assert [node["uuid"] for node in tree["nodes"]] == DEVICE_IDS[:2]
    assert tree["nodes"][0]["status"] == "UPDATED"
    assert tree["nodes"][0]["attributes"] == [
        {"name": "serial", "status": "UPDATED", "contains_conflict": False}
    ]


async def test_tree_without_a_diff_is_none(ih):
    assert await ih.diff.tree("main") is None


async def test_the_tree_selection_stays_bounded(ih, fake):
    """A DiffNode's relationships carry their elements and each element
    its properties, so the whole subtree is left out."""
    await ih.diff.tree("feature-x")
    query = last_query(fake)
    assert "relationships {" not in query
    assert "properties" not in query
    # num_unchanged is a summary-only field.
    assert "num_unchanged" not in query


async def test_tree_nodes_name_their_parent(ih):
    """`parent` is what include_parents=True fills in, so it is selected."""
    tree = await ih.diff.tree("feature-x", include_parents=True)
    assert tree is not None
    assert tree["nodes"][0]["parent"]["relationship_name"] == "site"
    assert tree["nodes"][1]["parent"] is None


async def test_tree_passes_the_window_and_paging(ih, fake):
    await ih.diff.tree(
        "feature-x",
        from_time="2026-08-01T00:00:00Z",
        to_time="2026-08-02T00:00:00Z",
        name="nightly",
        offset=1,
        limit=5,
        include_parents=True,
    )
    _, name, args, _ = parse_query(last_query(fake))
    assert name == "DiffTree"
    assert args == {
        "branch": "feature-x",
        "from_time": "2026-08-01T00:00:00Z",
        "to_time": "2026-08-02T00:00:00Z",
        "name": "nightly",
        "offset": 1,
        "limit": 5,
        "include_parents": True,
    }


async def test_tree_omits_unset_arguments(ih, fake):
    await ih.diff.tree("feature-x")
    _, _, args, _ = parse_query(last_query(fake))
    assert args == {"branch": "feature-x"}


async def test_summary_takes_no_paging(ih, fake):
    await ih.diff.summary("feature-x", from_time="2026-08-01T00:00:00Z")
    _, name, args, _ = parse_query(last_query(fake))
    assert name == "DiffTreeSummary"
    assert args == {"branch": "feature-x", "from_time": "2026-08-01T00:00:00Z"}


async def test_status_filters_render_as_enum_tokens(ih, fake):
    """IncExclFilterStatusOptions takes DiffAction enums, and graphene
    rejects a quoted literal where an enum is declared."""
    await ih.diff.tree("feature-x", filters={"status": {"includes": ["ADDED"]}})
    assert "filters: {status: {includes: [ADDED]}}" in last_query(fake)


async def test_kind_filters_stay_quoted_strings(ih, fake):
    await ih.diff.tree("feature-x", filters={"kind": {"includes": ["TestingDevice"]}})
    assert 'filters: {kind: {includes: ["TestingDevice"]}}' in last_query(fake)


async def test_the_branch_is_an_argument_not_a_path(ih, fake):
    """DiffTree takes `branch:`, so the request is not branch-scoped."""
    await ih.diff.tree("feature-x")
    assert fake.requests[-1].url.path == "/graphql"


async def test_files_reads_the_rest_route(ih, fake):
    files = await ih.diff.files("feature-x")
    assert files["main"][0]["location"] == "topology.j2"
    request = fake.requests[-1]
    assert request.url.path == "/api/diff/files"
    assert request.url.params["branch"] == "feature-x"


async def test_artifacts_reads_the_rest_route(ih, fake):
    artifacts = await ih.diff.artifacts("feature-x")
    assert artifacts[ARTIFACT_ID]["action"] == "updated"
    request = fake.requests[-1]
    assert request.url.path == "/api/diff/artifacts"
    assert request.url.params["branch"] == "feature-x"
