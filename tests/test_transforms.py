import pytest
from conftest import make_api

import aiopyinfrahub

TRANSFORM = "device-report"


async def test_render_python_decodes_the_output(ih, fake):
    result = await ih.transforms.render_python(TRANSFORM)
    assert result["transform"] == TRANSFORM
    assert fake.requests[-1].url.path == f"/api/transform/python/{TRANSFORM}"


async def test_extra_params_ride_along_as_variables(ih, fake):
    result = await ih.transforms.render_python(TRANSFORM, device="sw-1", limit=5)
    assert result["params"] == {"device": "sw-1", "limit": "5"}
    params = fake.requests[-1].url.params
    assert params["device"] == "sw-1"
    assert params["limit"] == "5"


async def test_render_jinja2_returns_text(ih, fake):
    rendered = await ih.transforms.render_jinja2(TRANSFORM, device="sw-1")
    assert isinstance(rendered, str)
    assert rendered.splitlines() == [f"# {TRANSFORM}", "device: sw-1"]
    assert fake.requests[-1].url.path == f"/api/transform/jinja2/{TRANSFORM}"


async def test_branch_and_at_are_query_params(ih, fake):
    await ih.transforms.render_python(TRANSFORM, branch="feature-x", at="2026-08-01")
    params = fake.requests[-1].url.params
    assert params["branch"] == "feature-x"
    assert params["at"] == "2026-08-01"


async def test_branch_and_at_are_not_variables(ih):
    """They address the read; the transform never sees them."""
    result = await ih.transforms.render_python(
        TRANSFORM, branch="feature-x", at="2026-08-01"
    )
    assert result["params"] == {}


async def test_the_client_branch_is_the_default(fake):
    async with make_api(fake, branch="feature-x") as ih:
        await ih.transforms.render_jinja2(TRANSFORM)
    assert fake.requests[-1].url.params["branch"] == "feature-x"


async def test_no_branch_sends_no_branch_param(ih, fake):
    await ih.transforms.render_python(TRANSFORM)
    assert "branch" not in fake.requests[-1].url.params


async def test_an_unknown_transform_raises(ih):
    with pytest.raises(aiopyinfrahub.RequestError) as excinfo:
        await ih.transforms.render_python("no-such-transform")
    assert excinfo.value.status_code == 404


async def test_transform_ids_are_percent_encoded(ih, fake):
    with pytest.raises(aiopyinfrahub.RequestError):
        await ih.transforms.render_jinja2("reports/device")
    assert "/api/transform/jinja2/reports%2Fdevice" in str(fake.requests[-1].url)
