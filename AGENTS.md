# AGENTS.md

Guidance for AI coding agents working in this repository. CLAUDE.md points
here; this file is the single source.

## Project

`aiopyinfrahub` - a fully async Infrahub API client, built from scratch with
httpx. It is inspired by the [pynetbox](https://github.com/netbox-community/pynetbox)
/ [pynautobot](https://github.com/nautobot/pynautobot) client lineage and by the
official [infrahub-sdk](https://github.com/opsmill/infrahub-sdk-python)
(Apache 2.0, OpsMill), but is **not a port** of either: the sync clients'
ergonomics depend on Python protocols that cannot be awaited, and the official
SDK makes a different set of tradeoffs (nine runtime dependencies, an identity
map, wrapper objects around every attribute). The API surface here is
deliberately different (see Design constraints below).

It is also the sister project to
[aiopynetbox](https://github.com/challey74/aiopynetbox) and
[aiopynautobot](https://github.com/challey74/aiopynautobot), same author, same
design constraints, same layout. **aiopynautobot is the structural reference
for anything that isn't Infrahub-specific** - match its module split, naming,
and idiom rather than inventing new ones, and port fixes between the three.
Infrahub's protocol is far enough from REST that the traversal layer differs;
everything below it should not.

Package layout: `src/aiopyinfrahub/`, tests in `tests/`. Managed with `uv`.

**Status: 0.1.0 in progress.** [PLAN.md](PLAN.md) is the pinned design: the
"Public surface" block there is the canonical usage example, and every
divergence from the sisters is written down under "Resolved decisions" so it is
not re-litigated. Read it before changing the surface.

## Commands

- `uv sync` - install/update the environment (Python 3.11+)
- `uv run pytest` - run tests (`uv run pytest tests/test_foo.py::test_bar` for one test)
- `uv run ruff check` / `uv run ruff format` - lint and format (line length 88, isort + ASYNC lint rules enabled)
- `uv run pyright` - type check (src only)

pytest-asyncio runs in `asyncio_mode = "auto"` - async test functions need no decorator.

## Design constraints (why this isn't just "infrahub-sdk with less")

These behaviors from the sync client lineage and from the official SDK are
impossible, wrong, or deliberately rejected here, and must NOT be replicated:

1. **Lazy I/O in `__getattr__`** - pynautobot fetches the full object when you
   touch a missing attribute. `__getattr__` cannot be async, so any such fetch
   is either a hidden blocking call or a coroutine nobody awaits. Here a
   missing attribute on a brief record raises `AttributeError` naming
   `await record.full_details()`, which re-queries `ids=[id]` on the record's
   own branch. `__getattr__` never does I/O. (infrahub-sdk is careful here
   too: its `RelatedNode.peer` resolves only from the client's store and
   raises otherwise, with fetching an explicit `await rel.fetch()`; its
   hidden state is the store itself, see constraint 4.)
2. **Properties that do I/O** - properties cannot await. Instance info is
   methods: `await ih.version()`, `await ih.schema()`, `await recordset.count()`.
3. **Eagerly materialized result lists** - infrahub-sdk's `client.all(kind)`
   drains every page and returns a `list`. Reads here are lazy async iterators
   (`async for d in ih.InfraDevice.all()`), so an early `break` stops fetching.
   Totals come from `await ih.InfraDevice.count()`, never `len()`.
4. **Implicit caching / identity map** - infrahub-sdk's `store` silently hands
   back previously seen nodes, and `prefetch_relationships` fires extra queries
   you did not ask for. Neither exists here. The only cache is the branch
   schema (explicitly refreshable), and every network round trip is a visible
   `await`. Hydrating a relationship that the default selection leaves out is
   spelled `await record.fetch("interfaces")`: one call, one query, no
   prefetching behind it.
5. **Attribute and relationship wrappers as objects** - infrahub-sdk keeps
   `{"value": ...}` as an `Attribute` and `{"node": ...}` /
   `{"count": n, "edges": [...]}` as a `RelationshipManager`, so reads are
   `node.name.value`. Both shapes are flattened at parse here
   ([PLAN.md](PLAN.md) decisions 5 and 6): `device.name == "atl1-edge1"`,
   `device.site` is a brief `Record`, `device.tags` is a `list[Record]`. The
   Record remembers which keys were wrapped (`_attr_keys`, `_rel_keys`) so
   serialization can rebuild the wire shape. Attribute and relationship
   metadata (`is_protected`, `is_default`, `updated_at`, `source`, `owner`) is
   not fetched by default; `properties=True` on a read opts in and
   `record.meta(name)` hands it back as a Record. Reads stay flattened either
   way, which is what made the opt-in additive rather than breaking, and
   metadata is never diffed or saved.
6. **A GraphQL parser dependency** - queries are rendered dict-to-text, no
   graphql-core, because httpx stays the only runtime dependency. That puts the
   injection surface on us: kind names, field names, and filter keys are
   validated as identifiers before rendering, and string values are inlined
   through `json.dumps` (GraphQL string literals share JSON's escape grammar).
   Never interpolate an unvalidated caller string into a query.
7. **Retry policy keyed on HTTP method** - the sisters retry only GETs, because
   in REST the method implies the semantics. Every node read here is a POST to
   `/graphql`, so that rule would never retry a read. `_request_response` takes
   `idempotent=` (defaulting to `method == "GET"`); the GraphQL query path
   passes `True`, mutations pass `False`.
8. **Treating HTTP 200 as success** - Infrahub answers 200 with an `errors`
   array when execution fails. The kind layer raises `GraphQLError` whenever
   the body carries errors, because `get()` and `all()` have no partial-data
   story. `ih.graphql.query()` deliberately does not raise: partial data plus
   `.errors` is house semantics for the raw layer.

Ideas worth keeping from both lineages (they are pure Python, no I/O):
diff-based `save()` (snapshot at parse, diff `serialize()`, send only what
changed), attribute-based traversal, the flat exception taxonomy, and, from
infrahub-sdk specifically, the dict-to-GraphQL rendering approach and its
production-tuned default field selection.

## Infrahub vs Nautobot/NetBox (do not copy the sisters blindly)

aiopynautobot is the structural reference, but these are REST-shaped
assumptions that must **not** be carried over:

- **REST CRUD does not exist.** Infrahub serves no REST routes for
  schema-defined objects: every read, create, update, and delete is a POST to
  `/graphql[/{branch}]`. REST covers schema, auth, info/config, storage,
  artifacts, transforms, and stored queries only. The sisters' core trick
  (URL-path traversal mirroring the object model) has nothing to traverse, so
  the axis becomes the **schema kind**: `ih.InfraDevice`, resolved against
  `GET /api/schema`, not `ih.infra.devices`.
- **`Authorization: Token <token>` is wrong.** Infrahub reads API tokens from
  `X-INFRAHUB-KEY: <token>` (exact spelling). `Authorization: Bearer` is the
  JWT path, which `username=`/`password=` takes; the two are mutually exclusive
  in the constructor because the server resolves an API key first and would
  ignore the credentials. `token=None` and no credentials omits the header
  entirely: Infrahub allows anonymous reads by default and rejects anonymous
  mutations.
- **Records have no `url` field**, so `(url, id)` cannot be the identity key.
  Identity is `(branch, id)`: ids are unique per branch, and the same id on two
  branches is two different states of the object. Records without an id
  compare by object identity, as in the sisters.
- **`save()` is a mutation, not a PATCH.** The snapshot-diff mechanism is
  unchanged, but the payload goes out as `<Kind>Update` with `{id, ...changed}`
  under `data`, changed attributes re-wrapped as `{"value": v}` and changed
  relationships as `RelatedNodeInput` (`{"id": ...}` /
  `[{"id": ...}]`). `delete()` sends `<Kind>Delete`, whose result has `ok` but
  **no `object`**. Generics get only `<Kind>Update`, never Create/Upsert.
- **Retries key on idempotency, not method** (see Design constraint 7). The
  rest of the policy is verbatim from the sisters: 429 retried for everything
  honoring `Retry-After` (capped at 60s), 502/503/504 and
  `httpx.TransportError` retried only when idempotent, capped exponential
  backoff with jitter.
- **HTTP 200 can carry errors.** `resp.is_success` is not the end of error
  handling on the GraphQL route. 401/403/404 still arrive as non-success HTTP
  and raise `RequestError` in the request core, but an execution failure is a
  200 whose body has an `errors` array.
- **Pagination is offset/limit with a `count` field**, not cursors and not
  `next` links. Page 1 of a `Paginated<Kind>` carries `count`; the remaining
  offsets are derived arithmetically and fanned out through the sliding window.
  Nothing follows a link.
- **`is_visible` does not exist** on current Infrahub. It was removed;
  `is_protected` is the only remaining flag property. Nothing in this client
  may reference it, in filters, selections, or inputs.

And these are Infrahub-specific additions with no counterpart in either sister:

- **Branches are addressed two different ways.** GraphQL takes the branch as a
  **URL path suffix** (`POST /graphql/{branch}`, `quote(branch, safe="")`);
  there is no `branch` query parameter on that route. REST takes it as a
  **query parameter** (`?branch=`). `api(branch=...)` sets the client-wide
  default and kind operations, `graphql.query()`, and `schema()` take a
  per-call `branch=` override. There is no branch-view object.
- **Time travel.** `at=` is a query parameter accepted by read operations and
  `graphql.query()`. Mutations do not accept it (the server resets it), so
  nothing on the write path should thread it through.
- **`hfid`, the human-friendly id.** `hfid: [String]` is a plain top-level
  filter, but it exists **only when the kind's schema defines
  `human_friendly_id`**. Check the schema before emitting it, and raise a clear
  error rather than sending a filter the server will reject. `hfid` is also
  accepted in `DeleteInput` and in Update/Upsert inputs.
- **The schema is the API.** Kinds, their attributes, their relationships,
  their `default_filter`, and their `human_friendly_id` all come from
  `GET /api/schema`. There is no OpenAPI document to generate hints from, so
  the shipped kind hints are generated from a live instance's schema instead
  (see Architecture below), and because kinds are instance-specific those hints
  are a completion aid, never a whitelist.
- **The top-level `Branch` query is a flat list**, `Branch(ids: [ID], name: String): [Branch!]!`,
  not the `count`/`edges`/`node` envelope every schema-defined kind uses. It is
  a genuine special case, hand-written in [branches.py](src/aiopyinfrahub/branches.py).
  The paginated `InfrahubBranch` variant is a different query and boxes its
  fields in `{value}`; do not confuse the two.
- **Filter naming is `<attr>__value`.** Attribute filters are
  `name__value` / `name__values` / `name__isnull`; relationship traversal is
  `<rel>__<peerfilter>` (`site__name__value`, `site__ids`, `site__isnull`).
  Top-level extras include `ids`, `offset`, `limit`, `order`, `partial_match`.
- **Kind names are `namespace + name`** (`InfraDevice`, `BuiltinTag`), with the
  `Attribute` namespace collapsing to the bare name. Mutation type names derive
  from the kind: `<Kind>Create/Update/Upsert/Delete`.

## Architecture

All HTTP funnels through `Api._send()` ([api.py](src/aiopyinfrahub/api.py)):
the `X-INFRAHUB-KEY` or `Authorization: Bearer` header, User-Agent, branch and
`at` placement, error raising (non-success -> `RequestError`), and the retry
loop (429 for everything honoring `Retry-After`; 502/503/504 and
`httpx.TransportError` only when `idempotent=True`; `Api(retries=)` bounds
attempts, `_backoff()` does capped exponential backoff with jitter) all live
there and nowhere else.

`Api._request_response()` is the auth wrapper layered over it, and it is what
every caller outside the auth routes goes through: a lazy single-flight login
(the first request that needs credentials takes `_auth_lock`, so concurrent
first requests produce one session, not one each), then one recovery attempt
on a 401 - refresh the access token and replay the request once, and if the
refresh itself 401s, log in again and replay once. `_login()`,
`_reauthenticate()`, and `logout()` call `_send()` directly, so a 401 from an
auth route is the answer rather than something to re-authenticate and replay.
Multipart parts travel through its `files=` argument rather than being posted
at the call site, so an upload still gets the headers, the auth, and the retry
policy. `Api._request()` adds JSON decoding (`_decode` -> `ContentError`) on
top of that. The layering is load-bearing twice over: the GraphQL path needs
the raw `httpx.Response` to inspect a 200 body that carries `errors`, and the
storage, artifact, and Jinja2-transform routes answer with bytes or text that
must not be decoded as JSON at all.

Module by module:

- [api.py](src/aiopyinfrahub/api.py) - `Api`: constructor (`url` bare host,
  positional; `token` positional-or-keyword; everything else keyword-only, with
  `username=`/`password=` rejected alongside `token=` because the server
  resolves an API key before a JWT and would silently ignore the credentials),
  the request core above, `__getattr__` mapping any non-underscore attribute to
  a fresh `KindEndpoint`, `kind(name)` as the string escape hatch, and eager
  wiring of `self.graphql`, `self.branches`, `self.tasks`, `self.pools`,
  `self.diff`, `self.graph`, `self.storage`, `self.artifacts`, and
  `self.transforms` (building a manager does no I/O, so eager wiring costs
  nothing). `version()` reads `GET /api/info`. `login()` / `logout()` drive the
  JWT session explicitly for callers who would rather not have it happen on the
  first request. `search(q)` yields brief Records from InfrahubSearchAnywhere
  and `convert_object_type()` wraps the mutation of that name. Schema
  management sits beside `schema()`: `load_schemas()` (which drops that
  branch's cache entry rather than refetching it, so the next read pays only if
  there is one), `check_schemas()`, `schema_in_sync()`, and
  `wait_schemas_converged()`, which polls because workers adopt a new schema on
  their own schedule and raises `ConvergenceTimeoutError`. No validation
  happens at attribute-access time; an unknown kind fails on the first awaited
  operation, with a message listing near-miss kinds from the schema.
- [kinds.py](src/aiopyinfrahub/kinds.py) - `KindEndpoint`:
  `get` / `filter` / `all` / `count` / `create` / `upsert`. `get(pk, /)` is
  positional-only so a filter named `pk` cannot collide: a UUID-shaped string
  queries `ids=[pk]`, anything else goes through the schema's `default_filter`
  (`ValueError` when the schema defines none). `get(**filters)` iterates and
  raises `ValueError` on a second match, returns `None` on zero. Create/upsert
  input shaping is schema-driven: attributes wrap scalars as `{"value": v}`,
  cardinality-one relationships wrap a string as `{"id": v}`, cardinality-many
  wrap `list[str]` as `[{"id": ...}]`, and a dict or list of dicts always
  passes through untouched so callers can hand over the full wire shape.
- [schema.py](src/aiopyinfrahub/schema.py) - the branch schema cache and the
  field-selection builder. The cache is keyed **per branch**
  (`dict[str | None, dict]`, `None` being the client-wide default) behind one
  `asyncio.Lock`, so concurrent first calls make one fetch. `nodes`,
  `generics`, `profiles`, and `templates` flatten into a single
  `{kind: node_schema}` map with `kind = namespace + name`.
  `ih.schema(branch=..., refresh=True)` forces a refetch. The default field
  selection (the GraphQL substitute for `SELECT *`) is `id`, `hfid`,
  `display_label`, `__typename`, every attribute as `name { value }`, and only
  those relationships whose schema kind is `Attribute` or `Parent`; other
  many-relationships are excluded to avoid fan-out. `include=` / `exclude=`
  adjust it per call, and `properties=True` widens every attribute and
  relationship with its metadata selection (`is_protected`, `is_default` on
  attributes only, `updated_at`, and `source`/`owner` as bare lineage ids, not
  hydrated peers).
- [graphql.py](src/aiopyinfrahub/graphql.py) - `GraphQLQuery`,
  `GraphQLRecord`, and the dict-to-text renderer. **The renderer is the
  injection guard**: every kind name, field name, and filter key is validated
  as an identifier before it reaches the output string, and string values are
  inlined via `json.dumps`. There is no variable promotion, so nothing else in
  the library can smuggle an unchecked identifier into a query. Any new call
  site that builds query text must go through the renderer, not f-strings.
  Two additions serve the managers: `EnumValue` is a `str` subclass rendered as
  a bare enum token instead of a quoted literal, because graphene rejects a
  quoted string where an enum is declared (`InfrahubTask(state:)`,
  DiffTree's `status`) - it still passes through the identifier guard, so it
  opens no hole; and `segment()` percent-encodes a value used as a **URL path
  segment** (`safe=""`, slashes included, since no Infrahub path segment may
  span two), which every caller value that lands in a path goes through, since
  httpx escapes query parameters and leaves the path alone. `stored()` runs a
  server-side CoreGraphQLQuery through `POST /api/query/{id}`, the one GraphQL
  call that is a REST route and therefore takes `?branch=` rather than a path
  suffix.
- [branches.py](src/aiopyinfrahub/branches.py) - `Branches`, wrapping the
  hand-written GraphQL: the flat `Branch(name:, ids:)` list query and the
  `BranchCreate` / `Delete` / `Update` / `Rebase` / `Merge` / `Validate`
  mutations, which take `wait_until_completion: true` **beside** `data`, not
  inside it. `BranchUpdate` returns only `ok`; `BranchDelete` has no `object`.
  With `wait=False` the payload's `object` would be a snapshot of work not yet
  done, so the selection collapses to `{ok, task {id}}` and the method returns
  the task id instead, to hand to `ih.tasks.wait()`.
- [tasks.py](src/aiopyinfrahub/tasks.py) - `Tasks` and `TaskSet` over the
  GraphQL-only `InfrahubTask` query. Task Records are plain: a task is not a
  schema kind, nothing in its payload is wrapped, and `save()`/`delete()` on
  one raise `ValueError`. `TaskSet` walks pages in order rather than fanning
  them out (a task list is short and its states move under the reader).
  `wait(id)` polls until the state leaves PENDING/RUNNING/SCHEDULED/PAUSED/
  CANCELLING, raising `TaskTimeoutError` at the deadline; `state=` filters are
  marked `EnumValue` so they render unquoted.
- [pools.py](src/aiopyinfrahub/pools.py) - `Pools`: `next_ip_address()` and
  `next_ip_prefix()` run the GetResource mutations and return a **brief**
  Record, because PoolAllocatedNode carries the allocation
  (id/kind/identifier/display_label/branch) and not the node's own fields.
  Allocation is a write, so nothing here takes `at`; an `identifier=` makes a
  repeat allocation return the same resource instead of consuming another.
  `utilization()` and `allocated()` return plain data. Pools are accepted as a
  Record or as an id.
- [diff.py](src/aiopyinfrahub/diff.py) - `Diff`: `tree()` and `summary()` over
  DiffTree / DiffTreeSummary, `files()` and `artifacts()` over `/api/diff/*`.
  All four return plain dicts and lists, because a diff is a report and Records
  are for nodes. The tree selection stops at each node's attribute names and
  statuses: relationships carry elements which carry properties, so selecting
  the whole tree fans out without bound. The branch is an argument or a query
  parameter here, never a URL path suffix.
- [graph.py](src/aiopyinfrahub/graph.py) - `Graph`: `paths()`, `path_exists()`
  (the cheap form, `max_paths: 1` with only `count` selected), and
  `reachable_nodes()`, thin wrappers over InfrahubPathTraversal and
  InfrahubReachableNodes. **Server >= 1.10 only**; an older instance answers
  `Cannot query field ...` in the errors array, which surfaces as
  `GraphQLError`. Results are plain data: a path describes the graph rather
  than being an object anyone saves.
- [storage.py](src/aiopyinfrahub/storage.py) - `Storage`: `get()`, `upload()`,
  `upload_file()` (multipart), and the CoreFileObject reads `get_file()`,
  `get_file_by_storage_id()`, and `get_file_by_hfid()`. Everything is bytes, so
  these go through `_request_response()` and never `_request()`. An identifier
  owned by a file object answers 403 on the object route; the by-storage-id
  route is the one that resolves the owning node's permissions first.
- [artifacts.py](src/aiopyinfrahub/artifacts.py) - `Artifacts`: `fetch()`
  returns one artifact's bytes, `generate()` queues a definition's artifacts
  and returns nothing, since the route answers with no body worth parsing
  (watch `ih.tasks` for how it went). Artifact *nodes* are an ordinary kind,
  `ih.CoreArtifact`; this manager is only about content.
- [transforms.py](src/aiopyinfrahub/transforms.py) - `Transforms`:
  `render_python()` decodes the JSON a Python transform produces,
  `render_jinja2()` returns the rendered text, since that route answers
  text/plain. Extra keyword arguments ride along as query parameters and become
  the transform's own GraphQL variables; `branch` and `at` are taken.
- [models.py](src/aiopyinfrahub/models.py) - `KIND_MODELS` (empty to start) and
  `register_model(kind, record_class)`, the public hook for mapping a kind to a
  `Record` subclass. Mirrors the sisters' `ENDPOINT_MODELS`.
- [response.py](src/aiopyinfrahub/response.py) - `Record` and `RecordSet`.
  `Record` snapshots a deep copy of `serialize()` after every parse; `updates()`
  diffs current against snapshot; `save()` sends only the diff. Hydration
  flattens attribute and relationship wrappers and records `_attr_keys` /
  `_rel_keys` so serialization can rebuild them. `_branch` and `_kind` are
  private attrs set at hydration and are what `full_details()` re-queries
  against. Top-level query results and mutation `object` payloads are
  `full=True`; nested peers are brief. A read passing `properties=True` keeps
  the rest of each wrapper in `_meta`, which `meta(name)` hands back (a Record
  for an attribute or a cardinality-one relationship, a list positioned against
  the peers for a cardinality-many one, since Infrahub hangs properties off
  each edge); metadata is never serialized, diffed, or saved. `fetch(name)`
  hydrates one relationship with a single `ids=[id]` + that relationship query
  and merges it in, refreshing only that key of the snapshot so a pending edit
  elsewhere survives - this is the explicit stand-in for
  `prefetch_relationships`. `add_related()` / `remove_related()` send
  RelationshipAdd / RelationshipRemove and accept peers as ids, Records, or
  RelatedNodeInput dicts; both answer with `ok` and nothing else, so re-read to
  see the result. `RecordSet` is a lazy re-iterable value
  object: constructing one does zero I/O, `__aiter__` returns a fresh
  generator, and `_iter()` fetches page 1 for `count`, then fans the remaining
  offsets out through a **sliding window** of at most `Api.max_concurrency`
  tasks, yielding in offset order and cancelling what is still in flight in a
  `finally`. An explicit `offset` pins the query to one page.
- [exceptions.py](src/aiopyinfrahub/exceptions.py) - flat taxonomy, every class
  subclassing `Exception` directly: `RequestError` (non-success HTTP),
  `ContentError` (successful response whose body is not JSON), `GraphQLError`
  (a body carrying `errors`), `TaskTimeoutError` (a task still active at
  `tasks.wait()`'s deadline, carrying `task_id` and `timeout`), and
  `ConvergenceTimeoutError` (the schema still propagating at
  `wait_schemas_converged()`'s deadline). No shared base class.

Import order is api -> kinds -> schema -> models -> response. Every manager
(`branches.py`, `graphql.py`, `tasks.py`, `pools.py`, `diff.py`, `graph.py`,
`storage.py`, `artifacts.py`, `transforms.py`) is imported by `api.py` at
runtime and imports `Api` back under `TYPE_CHECKING` only; `response.py`
TYPE_CHECKING-imports the others. Keep it one-way; the schema cache living on
`Api` and the renderer living in `graphql.py` is what makes that possible.

The kind hints are generated, and split across two files:
`kinds_generated.py` is a bare-annotations mixin (`kind: KindEndpoint` lines
and nothing else) that `Api` inherits, and `hints_generated.pyi` is stub-only,
carrying the per-kind overloads that never exist at runtime. Both come from
`scripts/generate_kinds.py` reading `GET /api/schema` on
https://sandbox.infrahub.app (anonymous reads are allowed there), are **never
edited by hand**, are guarded by [tests/test_generated.py](tests/test_generated.py),
and are refreshed weekly by
[.github/workflows/regenerate-kinds.yml](.github/workflows/regenerate-kinds.yml),
which opens a PR. The hints never restrict what you can reach at runtime:
`Api.__getattr__` stays the only mechanism, so an instance-specific kind the
sandbox has never heard of resolves exactly as before, and fallback overloads
keep unknown kind names legal for the type checker too. That is what makes a
stale generated file a missing hint rather than a breakage.

Tests run entirely against `FakeInfrahub` in
[tests/conftest.py](tests/conftest.py) - an in-memory Infrahub behind
`httpx.MockTransport` (no network, no mocking library). It serves
`GET /api/schema` and `GET /api/info`, the auth routes with **expirable JWTs**
(so the 401 -> refresh -> replay and refresh-401 -> relogin paths are
exercised rather than asserted about), and the storage, artifact, transform,
stored-query, schema load/check and diff REST routes. On the GraphQL side it
executes enough of the surface to round-trip CRUD, pagination, branch
operations, tasks (whose states advance as they are polled), pool allocation
against a shrinking free list, diffs, graph traversal, and search. A
`fail_next` queue drives the retry tests and a `requests` list is the
assertion surface. Extend it when adding behaviors rather than reaching for a
mock.

[tests/test_demo_integration.py](tests/test_demo_integration.py) is the one
exception, and it is opt-in: the whole module is skipped unless
`AIOPYINFRAHUB_DEMO_URL` is set. `AIOPYINFRAHUB_DEMO_TOKEN` has no default
(the sandbox serves anonymous reads), and the write tests need both
`AIOPYINFRAHUB_DEMO_WRITES=1` and a token, since Infrahub rejects anonymous
mutations. Two rules there: **never assert an exact object count or a
dataset-specific name** (the sandbox's dataset mutates and no reset schedule
is published), and keep the request volume low, roughly a dozen per run.

Not implemented yet (deliberately, add only when needed):

- Cookie auth and SSO (OAuth2/OIDC). `token=` and `username=`/`password=` are
  the two authentication paths.
- Bulk `RecordSet.update()`/`delete()` (no server-side bulk mutation; would be
  N mutations behind a semaphore)
- CoreFileObject **uploads** (GraphQL multipart, version-gated >= 1.8, niche).
  The downloads are in `ih.storage`, as is the plain object store.
- WebSocket subscriptions

Separately from that list, a set of infrahub-sdk capabilities is rejected
outright rather than deferred (a sync facade, the store/identity map, a batch
executor, `prefetch_relationships`, recorder/playback, the CLI and per-kind
codegen, git operations, transport config knobs). Each is written up with its
rationale in [PLAN.md](PLAN.md) under Phase 2's "Rejected as paradigm
violations"; read it there rather than re-deriving it, and change that list
before adding any of them.

## Conventions

- httpx `AsyncClient` is the only HTTP transport, and httpx is the only runtime
  dependency. Nothing else, ever: no pydantic, no graphql-core, no tenacity
  (retries are hand-rolled), no respx (the fake server is hand-rolled).
- The client is usable as an async context manager
  (`async with aiopyinfrahub.api(...) as ih:`) so the connection pool closes
  deterministically. The context manager is **one-shot**; `aclose()` closes
  only clients the Api created, since a `client=` passed in is the caller's to
  close (httpx convention). `timeout=` is ignored when `client=` is given, and
  the docstring says so.
- No sync wrapper/facade unless explicitly requested.
- Fully type-annotated, `from __future__ import annotations` everywhere, ships
  `py.typed`.
- Tests run against an in-memory fake behind `httpx.MockTransport` - no
  network, no mocking library.
- Docstrings are Google style: one-line module and class summaries, `Args:` on
  the class rather than `__init__`, and `Raises:` listing only conditions
  beyond the global contract declared in the `Api` class docstring. Comments
  explain why and cite the source (a server version, an upstream client's
  behavior, a specific failure mode).
- Never vendor infrahub-sdk, pynautobot, or pynetbox code without carrying its
  Apache 2.0 header and updating [NOTICE](NOTICE).
