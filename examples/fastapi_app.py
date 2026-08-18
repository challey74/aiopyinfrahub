"""A runnable FastAPI app sharing one aiopyinfrahub client.

The Api async context manager is one-shot: enter it for the application's
lifetime rather than per request, so the httpx2 connection pool is reused,
the branch schema is fetched once instead of per request, and everything is
closed deterministically on shutdown. One Api instance is safe to share
across concurrent requests.

Run with:

    uv run --with fastapi --with uvicorn uvicorn examples.fastapi_app:app

Configure with INFRAHUB_URL and INFRAHUB_TOKEN.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException

import aiopyinfrahub

INFRAHUB_URL = os.environ.get("INFRAHUB_URL", "http://localhost:8000")
INFRAHUB_TOKEN = os.environ.get("INFRAHUB_TOKEN", "")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    async with aiopyinfrahub.api(INFRAHUB_URL, token=INFRAHUB_TOKEN) as ih:
        app.state.ih = ih
        yield  # handlers use app.state.ih; the pool closes on shutdown


app = FastAPI(lifespan=lifespan)


@app.get("/version")
async def version() -> dict[str, str]:
    return {"version": await app.state.ih.version()}


@app.get("/devices")
async def devices(
    site: str | None = None, branch: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    ih = app.state.ih
    query = (
        ih.InfraDevice.filter(site__name__value=site, branch=branch)
        if site
        else ih.InfraDevice.all(branch=branch)
    )
    found: list[dict[str, Any]] = []
    async for device in query:
        found.append({"id": device.id, "name": device.name})
        if len(found) >= limit:
            # Breaking out cancels the page fetches still in flight rather
            # than draining the rest of the result set.
            break
    return found


@app.get("/devices/{device_id}")
async def device(device_id: str, branch: str | None = None) -> dict[str, Any]:
    found = await app.state.ih.InfraDevice.get(device_id, branch=branch)
    if found is None:
        raise HTTPException(status_code=404, detail="device not found")
    return dict(found)


@app.get("/device-count")
async def device_count(role: str | None = None) -> dict[str, int]:
    ih = app.state.ih
    if role:
        return {"count": await ih.InfraDevice.count(role__value=role)}
    return {"count": await ih.InfraDevice.count()}


@app.get("/search")
async def search(q: str, limit: int = 10) -> list[dict[str, Any]]:
    # Hits are brief Records carrying only id and kind, so anything else
    # needs an explicit full_details() on the hit.
    return [
        {"id": hit.id, "kind": hit.kind}
        async for hit in app.state.ih.search(q, limit=limit)
    ]


@app.post("/ip-addresses")
async def allocate_ip(
    pool: str, identifier: str | None = None, branch: str | None = None
) -> dict[str, Any]:
    # An identifier makes the allocation repeatable: asking again returns the
    # same address instead of consuming another, so a retried request does not
    # drain the pool.
    try:
        address = await app.state.ih.pools.next_ip_address(
            pool, identifier=identifier, branch=branch
        )
    except aiopyinfrahub.GraphQLError as e:
        # An exhausted pool is an execution failure, which Infrahub answers
        # with HTTP 200 and an `errors` array rather than an error status.
        raise HTTPException(status_code=409, detail=str(e.errors)) from e
    return {"id": address.id, "address": address.display_label}


@app.patch("/devices/{device_id}/name")
async def rename(
    device_id: str, name: str, branch: str | None = None
) -> dict[str, Any]:
    found = await app.state.ih.InfraDevice.get(device_id, branch=branch)
    if found is None:
        raise HTTPException(status_code=404, detail="device not found")
    found.name = name
    # Only the changed field goes into the InfraDeviceUpdate mutation.
    changed = await found.save()
    return {"changed": changed, "name": found.name}
