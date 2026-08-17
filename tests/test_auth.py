import asyncio

import pytest
from conftest import BASE, make_api

import aiopyinfrahub


def jwt_api(fake, **kwargs):
    """A client holding credentials rather than an API token."""
    return make_api(fake, token=None, username="otto", password="infrahub", **kwargs)


def logins(fake):
    return [r for r in fake.requests if r.url.path == "/api/auth/login"]


def test_token_and_credentials_are_mutually_exclusive(fake):
    """The server resolves an API key before a JWT, so holding both would
    quietly ignore the credentials."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        aiopyinfrahub.api(BASE, token="x", username="otto", password="infrahub")


async def test_first_request_logs_in_and_sends_bearer(fake):
    async with jwt_api(fake) as ih:
        assert not fake.requests  # constructing the client does no I/O
        await ih.version()
    assert logins(fake)[0].url.path == "/api/auth/login"
    assert fake.requests[-1].headers["Authorization"] == "Bearer access-1"
    assert "X-INFRAHUB-KEY" not in fake.requests[-1].headers


async def test_login_is_single_flight(fake):
    """Concurrent first requests queue behind one login, not one each."""
    async with jwt_api(fake) as ih:
        await asyncio.gather(ih.version(), ih.version(), ih.version())
    assert fake.logins == 1


async def test_eager_login(fake):
    async with jwt_api(fake) as ih:
        await ih.login()
        assert fake.logins == 1
        await ih.version()
    assert fake.logins == 1


async def test_login_without_credentials_raises(ih):
    with pytest.raises(ValueError, match="needs credentials"):
        await ih.login()


async def test_bad_credentials_surface(fake):
    async with make_api(fake, token=None, username="otto", password="wrong") as ih:
        with pytest.raises(aiopyinfrahub.RequestError) as excinfo:
            await ih.version()
    assert excinfo.value.status_code == 401


async def test_expired_access_token_refreshes_and_retries(fake):
    async with jwt_api(fake) as ih:
        await ih.version()
        fake.expire_access_token()
        assert await ih.version() == "1.10.8"
        assert ih._access_token == "access-r1"
    assert fake.refreshes == 1
    assert fake.logins == 1


async def test_refresh_sends_the_refresh_token_as_bearer(fake):
    """The refresh route reads the token from the header and takes no body."""
    async with jwt_api(fake) as ih:
        await ih.version()
        fake.expire_access_token()
        await ih.version()
    refresh = next(r for r in fake.requests if r.url.path == "/api/auth/refresh")
    assert refresh.headers["Authorization"] == "Bearer refresh-1"
    assert not refresh.content


async def test_dead_refresh_token_logs_in_again(fake):
    async with jwt_api(fake) as ih:
        await ih.version()
        fake.expire_access_token()
        fake.expire_refresh_token()
        assert await ih.version() == "1.10.8"
    assert fake.refreshes == 0  # the refresh itself was rejected
    assert fake.logins == 2


async def test_a_401_after_reauthenticating_surfaces(fake):
    """The recovery is one refresh-and-retry, not a loop."""
    async with jwt_api(fake) as ih:
        await ih.version()
        # A 401, then a real refresh, then a 401 on the replayed request.
        fake.fail_next = [401, None, 401]
        with pytest.raises(aiopyinfrahub.RequestError) as excinfo:
            await ih.version()
    assert excinfo.value.status_code == 401
    assert fake.refreshes == 1


async def test_concurrent_401s_reauthenticate_once(fake):
    """The second task finds the token already replaced and just retries."""
    async with jwt_api(fake) as ih:
        await ih.version()
        fake.expire_access_token()
        await asyncio.gather(ih.version(), ih.version())
    assert fake.refreshes == 1


async def test_logout_drops_both_tokens(fake):
    async with jwt_api(fake) as ih:
        await ih.version()
        await ih.logout()
        assert ih._access_token is None
        assert ih._refresh_token is None
        assert fake.requests[-1].url.path == "/api/auth/logout"
        # The credentials survive, so the next request opens a new session.
        await ih.version()
    assert fake.logins == 2


async def test_logout_without_a_session_sends_nothing(ih, fake):
    """An API token is not a session; there is nothing to end."""
    await ih.logout()
    assert not fake.requests


async def test_api_token_401_does_not_reauthenticate(ih, fake):
    """A rejected API key stays rejected however often it is presented."""
    fake.fail_next = [401]
    with pytest.raises(aiopyinfrahub.RequestError):
        await ih.version()
    assert not logins(fake)


async def test_password_is_never_public(fake):
    """It is stored privately and nothing renders it."""
    fake.user = ("otto", "s3kr1t")
    async with make_api(fake, token=None, username="otto", password="s3kr1t") as ih:
        await ih.version()
        assert "password" not in ih.__dict__
        assert "s3kr1t" not in repr(ih)
        assert "s3kr1t" not in str(ih.branches)
