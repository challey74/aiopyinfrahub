# aiopyinfrahub

[![CI](https://github.com/challey74/aiopyinfrahub/actions/workflows/ci.yml/badge.svg)](https://github.com/challey74/aiopyinfrahub/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/aiopyinfrahub)](https://pypi.org/project/aiopyinfrahub/)
[![Python versions](https://img.shields.io/pypi/pyversions/aiopyinfrahub)](https://pypi.org/project/aiopyinfrahub/)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](https://github.com/challey74/aiopyinfrahub/blob/main/LICENSE)

Fully async Infrahub API client for Python, built on
[httpx2](https://github.com/pydantic/httpx2) (Pydantic's maintained
continuation of httpx), with httpx2 as its **only** runtime dependency (the
official `infrahub-sdk` carries nine).

Inspired by the pynetbox/pynautobot client lineage and by the official
[infrahub-sdk](https://github.com/opsmill/infrahub-sdk-python), and a sister
project to [aiopynetbox](https://github.com/challey74/aiopynetbox) and
[aiopynautobot](https://github.com/challey74/aiopynautobot). This is not a port
of any of them: the sync clients' ergonomics (lazy attribute fetches, eagerly
materialized result lists, sync pagination) depend on Python protocols that
cannot be awaited, and the official SDK makes different tradeoffs (an identity
map populated by default on every read, wrapper objects around every
attribute, eagerly materialized result lists). **All I/O is explicit and
awaitable, and nothing does network I/O behind your back.**

## Requirements

- Python 3.11+
- Infrahub 1.3+

## Installation

```sh
uv add aiopyinfrahub   # or: pip install aiopyinfrahub
```

## Quick start

```python
import asyncio
import aiopyinfrahub


async def main():
    async with aiopyinfrahub.api("https://infrahub.example.com", token="...") as ih:
        # attribute access does no I/O; the first awaited operation fetches
        # and caches the branch schema
        device = await ih.InfraDevice.get(name__value="atl1-edge1")
        print(device.name)  # "atl1-edge1", wrappers are flattened
        print(device.site.display_label)  # brief related Record

        # lazy async iteration, pages fetched concurrently
        async for d in ih.InfraDevice.filter(site__name__value="atl1"):
            print(d.name)

        # diff-based save: only changed fields go into InfraDeviceUpdate
        device.name = "atl1-edge2"
        await device.save()


asyncio.run(main())
```

### Try it against the public sandbox

OpsMill's [sandbox](https://sandbox.infrahub.app) allows anonymous reads, so
the whole read surface works with no token and no instance of your own:

```python
async with aiopyinfrahub.api("https://sandbox.infrahub.app") as ih:
    async for device in ih.InfraDevice.all(limit=10):
        print(device.name)
```

Writes need credentials, so point them at your own instance.

## Coming from infrahub-sdk

Kind-based access, diff-based `save()`, and branch/time-travel semantics all
carry over. What changes is that implicit I/O becomes explicit, results become
lazy, and the wrappers get out of your way:

| infrahub-sdk                                       | aiopyinfrahub                                          |
| -------------------------------------------------- | ------------------------------------------------------ |
| `await client.all("InfraDevice")` (returns a list)  | `async for d in ih.InfraDevice.all()`                  |
| `await client.get("InfraDevice", name__value="x")`  | `await ih.InfraDevice.get(name__value="x")`            |
| `await client.filters("InfraDevice", **filters)`    | `ih.InfraDevice.filter(**filters)`                     |
| `len(await client.all("InfraDevice"))`              | `await ih.InfraDevice.count()`                         |
| `node.name.value` / `node.site.peer`                | `device.name` / `device.site`                          |
| `await client.execute_graphql(query, variables)`    | `await ih.graphql.query(query, variables)`             |
| `await client.branch.all()`                         | `await ih.branches.list()`                             |
| `Config(default_branch=...)`, `branch=` per call    | `api(url, branch=...)`, `branch=` per call             |
| `await client.object_store.get(identifier)`         | `await ih.storage.get(identifier)` (bytes)             |
| `await client.allocate_next_ip_address(...)`        | `await ih.pools.next_ip_address(pool)`                 |
| `await client.task.wait_for_completion(id)`         | `await ih.tasks.wait(task_id)`                         |
| `client.schema.load()` + convergence wait           | `await ih.load_schemas()` + `wait_schemas_converged()` |
| `await client.login()` before requests              | automatic on the first request needing it              |
| `client.store.get(id)` (identity map)               | not included: every read is an explicit request        |
| `batch = client.create_batch()`                     | `asyncio.gather` + `Semaphore` (recipe below)          |
| nine runtime dependencies                           | one (httpx2)                                           |

The kind name is an attribute, so `ih.InfraDevice` is the whole traversal.
`ih.kind("InfraDevice")` is the escape hatch when the kind is held in a string.

Nested records come back *brief* (just `id`, `hfid`, `display_label`,
`__typename`, as Infrahub sends them). Touching a field that isn't loaded
raises `AttributeError` telling you to `await record.full_details()`. It never
fires a hidden request.

### Concurrency without a batch API

There is no `create_batch()`. `asyncio.gather` with a `Semaphore` is the whole
abstraction, it is one import you already have, and the concurrency limit stays
visible at the call site instead of inside a client object:

```python
sem = asyncio.Semaphore(10)


async def retire(device):
    async with sem:
        device.status = "retired"
        await device.save()


devices = [d async for d in ih.InfraDevice.filter(role__value="edge")]
await asyncio.gather(*(retire(d) for d in devices))
```

Reads are already concurrent inside one result set (pages are fanned out
through `max_concurrency`), so reach for this when you are writing, or when you
are querying several kinds at once.

## Features

- **Explicit async everywhere**: `httpx2.AsyncClient` under the hood, used as an
  async context manager so the connection pool closes deterministically.
  Nothing is lazily fetched behind an attribute access or a property.
- **Two ways in**: `token=` sends `X-INFRAHUB-KEY` exactly as before, and
  `username=`/`password=` runs a JWT session instead: one lazy login on the
  first request that needs it (concurrent first requests share it rather than
  racing for a session each), a refresh-and-replay on a 401, and a fresh login
  if the refresh has expired too. `await ih.login()` / `logout()` are there for
  callers who want the timing in their own hands.
- **Concurrent pagination**: Infrahub's `count` comes back with page 1, so the
  remaining offsets are fanned out through a sliding window bounded by
  `max_concurrency` (default 4) and yielded in order. Breaking out of an
  iteration early stops the fetching instead of buffering the rest.
- **Diff-based writes**: `save()` sends `<Kind>Update` carrying only the fields
  you actually changed, attributes re-wrapped as `{"value": ...}` and
  relationships as `{"id": ...}`, so concurrent edits to other fields are not
  clobbered.
- **Metadata when you ask for it**: `properties=True` on a read also fetches
  each attribute's `is_protected`, `is_default`, `updated_at`, `source`, and
  `owner` (relationships carry the same minus `is_default`), and
  `record.meta("name")` hands them back. Reads stay flattened either way
  (`device.name` is still the value), and metadata is never diffed or saved.
- **Explicit relationship reads and writes**: relationships the schema doesn't
  mark `Attribute` or `Parent` stay out of the default selection so list
  queries do not fan out, so `await device.fetch("interfaces")` is how you pull
  one in: one call, one query, nothing prefetched behind your back.
  `await device.add_related(...)` and `remove_related(...)` send
  RelationshipAdd/Remove and take peers as ids, Records, or wire-shape dicts.
- **Tasks you can wait on**: `ih.tasks` lists, counts, and gets Infrahub's
  server-side tasks, and `await ih.tasks.wait(task_id)` polls one to a terminal
  state or raises `TaskTimeoutError`. Branch operations called with
  `wait=False` return the queued task's id to feed straight into it.
- **Resource pools**: `await ih.pools.next_ip_address(pool)` and
  `next_ip_prefix(pool)` allocate; passing `identifier=` makes a repeat
  allocation hand back the same resource instead of consuming another.
  `utilization()` and `allocated()` report on a pool as plain data.
- **Diffs**: `ih.diff.tree(branch)` and `summary(branch)` over the GraphQL diff
  queries, `files(branch)` and `artifacts(branch)` over the REST routes. All
  four return plain dicts, because a diff is a report and Records are for
  nodes.
- **Storage, artifacts, and transforms**: `ih.storage` reads and writes the
  object store and downloads `CoreFileObject` content as bytes; `ih.artifacts`
  fetches generated artifacts and queues regeneration; `ih.transforms` renders
  a server-side Python transform (decoded) or Jinja2 template (as text), with
  extra keyword arguments becoming the transform's query variables.
- **Schema management**: `load_schemas()` and `check_schemas()` sit beside
  `schema()`, and because workers adopt a new schema asynchronously,
  `schema_in_sync()` and `wait_schemas_converged()` are what you wait on before
  reading against it. A load drops that branch's cached schema.
- **Search and graph traversal**: `ih.search("atl1")` lazily yields brief
  Records across every kind, and `ih.graph` walks relationships with `paths()`,
  `path_exists()`, and `reachable_nodes()` (server 1.10+).
- **Branches and time travel**: `api(url, branch="feature-x")` sets the default,
  and every read, `graphql.query()`, and `schema()` takes a per-call `branch=`.
  `at="2026-08-01T00:00:00Z"` reads the graph as it was. `ih.branches` creates,
  merges, rebases, validates, and deletes.
- **Schema-driven kind access**: `ih.InfraDevice` resolves against
  `GET /api/schema`, cached per branch behind a lock so concurrent first calls
  make one fetch. `default_filter` powers `get("atl1-edge1")`, and `hfid`
  filtering lights up for kinds that define `human_friendly_id`.
- **Raw GraphQL is first-class, not a fallback**:
  `await ih.graphql.query("query { InfraDevice { count } }")` returns a
  `GraphQLRecord` with `.data` and `.errors`, so partial results stay reachable.
  `await ih.graphql.stored(name_or_id, variables={...})` runs a query stored on
  the server on the same terms.
- **Retries with backoff**: 429 is retried for every request (honoring
  `Retry-After`); transient 502/503/504 and connection failures are retried for
  idempotent requests only. Because every node read is a POST to `/graphql`,
  the policy keys on idempotency rather than HTTP method. Exponential backoff
  with jitter; tune with `retries=`, disable with `retries=0`.
- **Custom models**: `aiopyinfrahub.register_model("InfraDevice", MyDevice)`
  maps a kind to your own `Record` subclass.
- **Typed**: full type hints and a `py.typed` marker. Kind hints generated
  from the public sandbox's schema ship in the box, so `ih.InfraDevice`
  autocompletes without any setup; run
  `uv run scripts/generate_kinds.py --url https://your-infrahub --token ...`
  to regenerate them from your own instance's schema. The hints never restrict
  what you can reach: any kind your instance defines keeps working, hinted or
  not.

## API tour

```python
# auth: token=... as below, or username=/password= for a JWT session that
# logs in on the first request that needs it and refreshes itself on a 401
async with aiopyinfrahub.api(url, token=token) as ih:
    # read
    device = await ih.InfraDevice.get(name__value="atl1-edge1")
    device = await ih.InfraDevice.get("atl1-edge1")  # via default_filter
    device = await ih.InfraDevice.get(hfid=["atl1-edge1"])
    total = await ih.InfraDevice.count(role__value="edge")
    async for d in ih.InfraDevice.filter(site__name__value="atl1"):
        ...
    async for d in ih.InfraDevice.all():
        ...

    # write
    new = await ih.InfraDevice.create(name="atl1-edge3", site=site_id)
    up = await ih.InfraDevice.upsert(name="atl1-edge3", site=site_id)
    device.name = "atl1-edge2"
    await device.save()  # InfraDeviceUpdate with only the changed fields
    await device.delete()  # InfraDeviceDelete

    # kinds held in strings
    async for t in ih.kind("BuiltinTag").all():
        ...

    # branches
    branches = await ih.branches.list()
    await ih.branches.create("feature-x")
    await ih.branches.merge("feature-x")

    # per-call branch and time overrides
    async for d in ih.InfraDevice.all(branch="feature-x"):
        ...
    async for d in ih.InfraDevice.all(at="2026-08-01T00:00:00Z"):
        ...

    # raw graphql
    result = await ih.graphql.query("query { InfraDevice { count } }")
    print(result.data, result.errors)

    # instance info
    schema = await ih.schema()  # cached per branch
    print(await ih.version())  # from GET /api/info

    # metadata: opt in per read, reachable through meta(), never saved
    device = await ih.InfraDevice.get("atl1-edge1", properties=True)
    print(device.name, device.meta("name").is_protected)

    # relationships outside the default selection are fetched explicitly
    interfaces = await device.fetch("interfaces")
    await device.add_related("tags", ["4b8f...", "9d21..."])

    # resource pools; identifier= makes the allocation repeatable
    address = await ih.pools.next_ip_address(pool_id, identifier="atl1-edge1-mgmt")
    print(address.display_label)

    # tasks: wait=False hands back the queued task's id to poll
    task_id = await ih.branches.merge("feature-x", wait=False)
    task = await ih.tasks.wait(task_id, timeout=120)
    print(task.state, task.conclusion)

    # object store round trip
    stored = await ih.storage.upload("interface Ethernet1\n")
    content = await ih.storage.get(stored["identifier"])  # bytes

    # what a branch changed, and a search across every kind
    print(await ih.diff.summary("feature-x"))
    async for hit in ih.search("atl1", limit=10):
        print(hit.id, hit.kind)  # brief: full_details() loads the rest
```

### Long-lived apps (FastAPI, services)

Create the client once and share it; the async context manager is one-shot, so
enter it for the app's lifetime, not per request:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    async with aiopyinfrahub.api(url, token=token) as ih:
        app.state.ih = ih
        yield  # handlers use `await app.state.ih...`; pool closes on shutdown
```

One shared instance is safe under concurrent requests, and the branch schema is
fetched once rather than per request. See
[examples/fastapi_app.py](https://github.com/challey74/aiopyinfrahub/blob/main/examples/fastapi_app.py)
for a runnable app.

### Custom httpx2 client

Pass your own `httpx2.AsyncClient` for custom SSL, proxies, event hooks, or
`MockTransport` in tests:

```python
client = httpx2.AsyncClient(verify="/path/to/ca.pem", timeout=60)
async with aiopyinfrahub.api(url, token=token, client=client) as ih:
    ...
```

Per httpx2 convention, a client you pass in is yours to close: `aclose()` and
the context manager only close clients the Api created itself, so one client
can safely back several Api instances.

Response caching is deliberately not built in: Infrahub is a source of truth,
and the library can't know your staleness tolerance. If you want HTTP caching,
pass a client with a caching transport and set the policy yourself. Note that
node reads are POSTs, which most HTTP caches will not cache.

## Development

Managed with [uv](https://docs.astral.sh/uv/):

```sh
uv sync              # install environment
uv run pytest        # tests (in-memory fake Infrahub, no network)
uv run ruff check    # lint
uv run ruff format   # format
uv run pyright       # type check
```

Live tests against a real instance are opt-in, off by default:

```sh
# reads only, anonymous against the public sandbox
AIOPYINFRAHUB_DEMO_URL=https://sandbox.infrahub.app uv run pytest tests/test_demo_integration.py

# writes too, against a disposable instance of your own
AIOPYINFRAHUB_DEMO_URL=http://localhost:8000 \
AIOPYINFRAHUB_DEMO_TOKEN=... AIOPYINFRAHUB_DEMO_WRITES=1 uv run pytest tests/test_demo_integration.py
```

`uv run scripts/generate_kinds.py` regenerates the kind hints
(`src/aiopyinfrahub/kinds_generated.py` and `hints_generated.pyi`) from a live
`/api/schema`; a weekly workflow does it against the sandbox and opens a PR.

See [CONTRIBUTING.md](https://github.com/challey74/aiopyinfrahub/blob/main/CONTRIBUTING.md).

## License

Apache 2.0, see
[LICENSE](https://github.com/challey74/aiopyinfrahub/blob/main/LICENSE) and
[NOTICE](https://github.com/challey74/aiopyinfrahub/blob/main/NOTICE).
