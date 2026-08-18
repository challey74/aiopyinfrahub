import httpx2
import pytest
from conftest import make_api

import aiopyinfrahub

QUERY = "query { Branch { name } }"


async def test_429_retried_for_a_read(ih, fake):
    fake.fail_next = [429]
    assert await ih.graphql.query(QUERY)
    assert len(fake.requests) == 2


async def test_429_retried_for_a_mutation(ih, fake):
    """429 means the request was rejected unprocessed, so writes are safe."""
    fake.fail_next = [429]
    await ih.branches.create("feature-y")
    assert len(fake.requests) == 2


async def test_503_retried_for_a_query(ih, fake):
    """Node reads are POSTs, so retries key on idempotency, not method."""
    fake.fail_next = [503, 503]
    assert await ih.graphql.query(QUERY)
    assert len(fake.requests) == 3


async def test_503_retried_for_a_kind_read(ih, fake):
    fake.fail_next = [503]
    assert await ih.TestingDevice.get("sw-1") is not None


async def test_503_not_retried_for_a_mutation(ih, fake):
    """An ambiguous write may already have been processed server-side."""
    fake.fail_next = [503]
    with pytest.raises(aiopyinfrahub.RequestError) as excinfo:
        await ih.branches.create("feature-y")
    assert excinfo.value.status_code == 503
    assert len(fake.requests) == 1


async def test_transport_error_retried_for_a_query(ih, fake):
    fake.fail_next = ["transport"]
    assert await ih.graphql.query(QUERY)


async def test_transport_error_not_retried_for_a_mutation(ih, fake):
    fake.fail_next = ["transport"]
    with pytest.raises(httpx2.ConnectError):
        await ih.branches.create("feature-y")


async def test_503_retried_for_a_rest_get(ih, fake):
    fake.fail_next = [503, 503]
    assert await ih.version() == "1.10.8"
    assert len(fake.requests) == 3


async def test_retries_are_bounded(fake):
    async with make_api(fake, retries=2) as ih:
        fake.fail_next = [503, 503, 503, 503]
        with pytest.raises(aiopyinfrahub.RequestError):
            await ih.graphql.query(QUERY)
    assert len(fake.requests) == 3


async def test_retries_can_be_disabled(fake):
    async with make_api(fake, retries=0) as ih:
        fake.fail_next = [429]
        with pytest.raises(aiopyinfrahub.RequestError):
            await ih.graphql.query(QUERY)
    assert len(fake.requests) == 1


def test_backoff_honors_retry_after(ih):
    assert ih._backoff(0, "3") == 3.0


def test_backoff_caps_retry_after(ih):
    assert ih._backoff(0, "9999") == 60.0


def test_backoff_falls_back_on_http_date(ih):
    # An HTTP-date Retry-After isn't parseable as seconds; use exponential.
    assert 0 < ih._backoff(0, "Wed, 21 Oct 2026 07:28:00 GMT") <= 0.5
