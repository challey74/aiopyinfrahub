import json

import pytest
from conftest import ARTIFACT_DEFINITION_ID, ARTIFACT_ID, DEVICE_IDS, make_api

import aiopyinfrahub


async def test_fetch_returns_the_content_as_bytes(ih, fake):
    content = await ih.artifacts.fetch(ARTIFACT_ID)
    assert content == b"interface Ethernet1\n  no shutdown\n"
    assert fake.requests[-1].url.path == f"/api/artifact/{ARTIFACT_ID}"


async def test_fetching_an_unknown_artifact_raises(ih):
    with pytest.raises(aiopyinfrahub.RequestError) as excinfo:
        await ih.artifacts.fetch("no-such-artifact")
    assert excinfo.value.status_code == 404


async def test_generate_posts_the_target_nodes(ih, fake):
    assert (
        await ih.artifacts.generate(ARTIFACT_DEFINITION_ID, nodes=[DEVICE_IDS[0]])
        is None
    )
    request = fake.requests[-1]
    assert request.url.path == f"/api/artifact/generate/{ARTIFACT_DEFINITION_ID}"
    assert json.loads(request.content) == {"nodes": [DEVICE_IDS[0]]}
    assert fake.generated[-1]["nodes"] == [DEVICE_IDS[0]]


async def test_generate_without_nodes_covers_every_target(ih, fake):
    await ih.artifacts.generate(ARTIFACT_DEFINITION_ID)
    assert json.loads(fake.requests[-1].content) == {"nodes": []}


async def test_generate_passes_the_branch(fake):
    async with make_api(fake, branch="feature-x") as ih:
        await ih.artifacts.generate(ARTIFACT_DEFINITION_ID)
    assert fake.requests[-1].url.params["branch"] == "feature-x"
    assert fake.generated[-1]["branch"] == "feature-x"


async def test_a_per_call_branch_wins(fake):
    async with make_api(fake, branch="main") as ih:
        await ih.artifacts.generate(ARTIFACT_DEFINITION_ID, branch="feature-x")
    assert fake.requests[-1].url.params["branch"] == "feature-x"


async def test_generating_an_unknown_definition_raises(ih):
    """The route takes an ArtifactDefinition id, not an artifact id."""
    with pytest.raises(aiopyinfrahub.RequestError) as excinfo:
        await ih.artifacts.generate(ARTIFACT_ID)
    assert excinfo.value.status_code == 404


async def test_ids_are_percent_encoded(ih, fake):
    with pytest.raises(aiopyinfrahub.RequestError):
        await ih.artifacts.fetch("a/b")
    assert "/api/artifact/a%2Fb" in str(fake.requests[-1].url)
