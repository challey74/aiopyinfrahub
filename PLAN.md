# PLAN.md

Build plan for `aiopyinfrahub`: a fully async Infrahub API client built on httpx,
sister project to [aiopynetbox](https://github.com/challey74/aiopynetbox) and
[aiopynautobot](https://github.com/challey74/aiopynautobot).

## Feasibility verdict (resolved 2026-08-17)

Infrahub has **no REST CRUD for schema-defined objects**. All node reads and
writes are GraphQL (`POST /graphql[/{branch}]`); REST covers schema, auth,
storage, artifacts, transforms, and stored queries. The sister libraries' core
trick (URL-path traversal mirroring the object model) therefore has nothing to
traverse. What transfers instead:

- Thin async httpx wrapper: yes. httpx stays the only runtime dependency
  (the official `infrahub-sdk` carries nine, including dulwich and pydantic).
- Dynamic attribute-based access: yes, but the axis is the **schema kind**
  (`ih.InfraDevice`), not app/endpoint. Kinds come from `GET /api/schema`,
  which is a machine-readable model the sisters never had.
- Record objects hydrated from JSON, diff-based save, explicit-async rules,
  sliding-window pagination, MockTransport testing: all carry over.

Verdict: build it. The house style adapts by swapping "URL traversal" for
"schema-kind lookup"; ergonomics land in the same place.

## Target

- Infrahub >= 1.3 semantics, written against 1.10.x behavior (verified from
  `opsmill/infrahub@develop` source, post-v1.10.6).
- Python 3.11+, httpx as the only runtime dependency, same tooling as the
  sisters (uv, ruff line 88 + I/ASYNC, pyright standard on src, pytest-asyncio
  auto mode).

## Public surface (pinned)

```python
import aiopyinfrahub

async with aiopyinfrahub.api("https://infrahub.example.com", token="...") as ih:
    # kind-based access; attribute access does no I/O, first awaited
    # operation fetches and caches the branch schema
    device = await ih.InfraDevice.get(name__value="atl1-edge1")
    device = await ih.InfraDevice.get("atl1-edge1")  # default_filter
    device = await ih.InfraDevice.get(hfid=["atl1-edge1"])
    async for d in ih.InfraDevice.filter(site__name__value="atl1"):
        ...
    async for d in ih.InfraDevice.all():
        ...
    n = await ih.InfraDevice.count(role__value="edge")

    print(device.name)  # "atl1-edge1" (attribute wrappers flattened)
    print(device.site.display_label)  # brief related Record
    device.name = "atl1-edge2"
    await device.save()  # InfraDeviceUpdate with only changed fields
    await device.delete()  # InfraDeviceDelete

    new = await ih.InfraDevice.create(name="atl1-edge3", site=site_id)
    up = await ih.InfraDevice.upsert(name="atl1-edge3", site=site_id)

    ih.kind("InfraDevice")  # escape hatch for kinds held in strings

    # branches (GraphQL mutations under the hood)
    branches = await ih.branches.list()
    await ih.branches.create("feature-x")
    await ih.branches.merge("feature-x")

    # per-call branch/time overrides; client-wide default via api(branch=...)
    async for d in ih.InfraDevice.all(branch="feature-x"):
        ...
    async for d in ih.InfraDevice.all(at="2026-08-01T00:00:00Z"):
        ...

    # raw GraphQL is first-class, not a fallback
    result = await ih.graphql.query("query { InfraDevice { count } }")
    print(result.data)

    # REST side
    schema = await ih.schema()  # cached per branch
    print(await ih.version())  # from GET /api/info
```

## Resolved decisions

Recorded here because each is a divergence someone will otherwise re-litigate.

1. **Kind traversal replaces app/endpoint traversal.** `Api.__getattr__` maps
   any non-underscore attribute to a `KindEndpoint` (fresh instance per access,
   like the sisters' Endpoint). No validation at access time; an unknown kind
   fails on the first awaited operation with a message listing near-miss kinds
   from the schema. `ih.kind(name)` is the string escape hatch.
2. **Auth is `X-INFRAHUB-KEY` only for 0.1.** JWT login/refresh is deferred
   (documented in Not implemented). `token=None` omits the header; Infrahub
   allows anonymous reads by default and rejects anonymous mutations.
3. **Branch addressing.** GraphQL: URL path suffix `/graphql/{branch}` with
   `quote(branch, safe="")`. REST: `?branch=` query param. `api(branch=...)`
   sets the client-wide default; kind operations, `graphql.query()`, and
   `schema()` accept a per-call `branch=` override. No branch-view object.
4. **Time travel.** `at=` is accepted by read operations and `graphql.query()`
   and becomes the `?at=` query param. Mutations do not accept `at` (server
   resets it).
5. **Attribute wrappers are flattened at parse.** `{"value": v, ...}` becomes
   the bare value, so `device.name == "atl1-edge1"` and assignment plus
   `save()` matches the sisters' feel. The Record remembers which keys were
   wrapped (`_attr_keys`) to re-wrap on serialize. Attribute metadata
   (`is_protected`, `source`, ...) is not fetched by default; a future
   `properties=True` mode can hydrate wrappers as Records instead.
6. **Relationships are flattened at parse.** Cardinality-one
   `{"node": {...}}` becomes a brief Record (None when node is null);
   cardinality-many `{"count": n, "edges": [{"node": ...}]}` becomes a
   `list[Record]`. The Record remembers rel keys (`_rel_keys`) so serialize
   emits `{"id": ...}` / `[{"id": ...}]` mutation inputs.
7. **Diff-based save via snapshot**, exactly the house mechanism. The payload
   is `{id, <changed attrs re-wrapped>, <changed rels as RelatedNodeInput>}`
   sent as `<Kind>Update`; the mutation selects the default field set on
   `object` and the response re-parses onto the Record.
8. **Create/upsert input shaping is schema-driven.** For each kwarg: attribute
   fields wrap scalars as `{"value": v}` (dicts pass through), rel-one wraps a
   string as `{"id": v}` (dict passes through), rel-many wraps `list[str]` as
   `[{"id": ...}]` (list of dicts passes through). Callers can always pass the
   full wire shape.
9. **get() semantics.** `get(pk, /)` positional: UUID-shaped strings query
   `ids=[pk]`, anything else queries the schema's `default_filter` (ValueError
   if the schema has none). `get(**filters)` iterates and raises ValueError on
   a second match, returns None on zero. `get(hfid=[...])` works when the
   schema defines `human_friendly_id` (it is a plain filter).
10. **Retry policy keys on idempotency, not HTTP method.** All node reads are
    POSTs to /graphql, so the sisters' GET-only rule would never retry reads.
    `_request_response(..., idempotent=...)` defaults to `method == "GET"`;
    the GraphQL query path passes `idempotent=True`, mutations `False`.
    Policy is otherwise verbatim: 429 retried for everything honoring
    Retry-After (capped 60s); 502/503/504 and `httpx.TransportError` retried
    only when idempotent; capped exponential backoff with jitter.
11. **Exception taxonomy** stays flat: `RequestError`, `ContentError`,
    `GraphQLError`. Infrahub answers HTTP 200 with an `errors` array on
    execution failures, so the kind layer raises `GraphQLError` whenever the
    body carries errors; the raw `ih.graphql.query()` returns a
    `GraphQLRecord` for 200 responses (partial data stays reachable via
    `.errors`, house semantics). 401/403/404 arrive as non-success HTTP and
    raise `RequestError` in the request core.
12. **Record identity** is `(branch, id)` (there is no `url` field to key on;
    ids are unique per branch, and the same id on two branches is two states).
    Records lacking an id compare by identity. `_branch` and `_kind` are
    private attrs set at hydration.
13. **full vs brief.** Top-level query results and mutation `object` payloads
    are `full=True` (relative to the default selection); nested peers are
    brief. A missing attribute on a brief record raises AttributeError
    pointing at `await record.full_details()`, which re-queries
    `ids=[id]` on `__typename` against the record's branch.
14. **Pagination** is offset/limit with `count` from page 1 (default page size
    50, Infrahub's own default), then the sliding-window fan-out bounded by
    `max_concurrency`, yielding in offset order, cancelling in-flight tasks on
    early break. An explicit `offset` pins a single page.
15. **Default field selection** (the GraphQL "SELECT *" substitute): `id`,
    `hfid`, `display_label`, `__typename`, every attribute as `name { value }`,
    and relationships whose schema kind is `Attribute` or `Parent`
    (one: `{ node { id hfid display_label __typename } }`, many: `{ count
    edges { node { id hfid display_label __typename } } }`). Other many-rels
    are excluded to avoid fan-out. Per-call `include=` / `exclude=` adjust.
    No `properties` in 0.1. This mirrors the official SDK's production-tuned
    defaults.
16. **GraphQL rendering** is dict-to-text (the official SDK's approach, no
    graphql-core). Strings inline via `json.dumps` (GraphQL string literals
    share JSON's escape grammar); filter keys, kind names, and field names are
    validated as identifiers before rendering, which closes the injection
    surface without variable promotion.
17. **Branch manager** (`ih.branches`) wraps the hand-written GraphQL:
    the flat `Branch(name:, ids:)` list query (a real special case: not
    edges/node), and `BranchCreate/Delete/Update/Rebase/Merge/Validate`
    mutations with `wait_until_completion: true` beside `data`.
    `BranchUpdate` returns only `ok`; `BranchDelete` has no `object`.
18. **Schema cache** is per-branch (`dict[str | None, dict]`) behind one
    `asyncio.Lock`, fetched from `GET /api/schema?branch=`. Kinds flatten
    `nodes + generics + profiles + templates` into one `{kind: node_schema}`
    map (kind = namespace + name). `ih.schema(branch=..., refresh=True)`
    forces a refetch. Schema load/check/convergence-poll are deferred.
19. **No generated hints in 0.1.** Kinds are instance-schema-specific (unlike
    Nautobot's fixed apps), so a static hints stub has less value; a generator
    reading a live instance's `/api/schema` is deferred.
    **Superseded:** sandbox.infrahub.app serves `/api/schema` anonymously, so
    the generator has a stable free source and 0.1 ships the hints after all.
20. **models.py** ships `KIND_MODELS: dict[str, type[Record]]` (empty to
    start) plus `register_model(kind, record_class)` so downstream apps can
    map kinds to Record subclasses, mirroring the sisters' hook.
21. **version()** reads `GET /api/info` (auth required; fine, we send the
    token). `config()` and the rest of the REST surface (storage, artifacts,
    transforms, stored queries, diff) are deferred.

## Modules

```
src/aiopyinfrahub/
    __init__.py     # exports, api = Api alias, __version__
    api.py          # Api: request core, retries, headers, branches/graphql wiring
    kinds.py        # KindEndpoint: get/filter/all/count/create/upsert (+ kind())
    schema.py       # per-branch schema cache, kind lookup, field selection builder
    graphql.py      # GraphQLQuery, GraphQLRecord, dict->GraphQL renderer
    branches.py     # Branches manager (flat Branch query + branch mutations)
    models.py       # KIND_MODELS, register_model
    response.py     # Record (flattening hydration, diff save), RecordSet
    exceptions.py   # RequestError, ContentError, GraphQLError
    py.typed
```

Import order: api -> kinds -> schema -> models -> response; branches and
graphql import api types under TYPE_CHECKING only; response TYPE_CHECKING-
imports the others (house rule).

## Phases

1. Skeleton: pyproject, exceptions, Api request core + retries, graphql
   renderer + GraphQLQuery/GraphQLRecord. Verify: unit tests for retry
   matrix, headers, renderer output.
2. Schema cache + KindEndpoint + Record/RecordSet with the flattening
   hydration and diff save. Verify: FakeInfrahub round-trips CRUD,
   pagination fan-out, brief/full behavior.
3. Branches manager. Verify: flat-list parse, mutation shapes.
4. Meta: README, AGENTS.md, CONTRIBUTING, CHANGELOG, SECURITY, NOTICE, CI,
   templates. Verify: full toolchain green (ruff check, ruff format --check,
   pyright, pytest, uv build).
5. Review pass (code review + security review by orchestrator), fixes,
   initial commits.

## Not implemented yet (deliberately, add only when needed)

Superseded by Phase 2 below for most entries; what stays out is listed
there under "Rejected as paradigm violations" with rationale.

- Cookie auth, SSO (OAuth2/OIDC)
- Bulk RecordSet.update()/delete() (no server-side bulk mutation; would be
  N mutations behind a semaphore)
- CoreFileObject uploads (GraphQL multipart; version-gated >= 1.8, niche)
- WebSocket subscriptions

## Phase 2: SDK feature parity in our paradigms (resolved 2026-08-17)

Decision: keep the library and close the functional gap with infrahub-sdk,
expressing every capability in the house style: explicit async, httpx-only,
lazy iteration, Records, flat exceptions. Hands-on comparison (see the
sdk-compare scratch report) grounded these mappings.

### Capability mapping

| SDK capability | Ours |
|---|---|
| JWT login/refresh | `api(username=, password=)` (exclusive with `token=`); lazy single-flight login on first request, Bearer header, refresh-then-relogin once on 401; explicit `await ih.login()` / `logout()` also public. Transport plumbing, like retries: not "hidden I/O". |
| Attribute/rel metadata | `properties=True` on read ops fetches `is_protected`, `is_default`, `updated_at`, `source`, `owner` (and rel `properties`); reads stay flattened (`device.name` is the value) and metadata lands on `record.meta("name")` -> Record. Never diffed, never saved. |
| prefetch_relationships | Rejected. Explicit `await record.fetch(rel_name)` hydrates one relationship via `ids=[id]` + `include=[rel]` and merges. |
| RelationshipAdd/Remove | `await record.add_related(rel_name, peers)` / `remove_related(rel_name, peers)` (peers: ids, Records, or RelatedNodeInput dicts). |
| Tasks | `ih.tasks` manager over `InfrahubTask`: `all()/filter()` (lazy), `get(id)`, `count()`, `wait(id, timeout=, interval=)` polling with asyncio.sleep; `TaskTimeoutError` joins the taxonomy (mirrors the sisters' JobTimeoutError). Branch ops with `wait=False` return the task id to feed `ih.tasks.wait()`. |
| Resource pools | `ih.pools`: `next_ip_address(pool, ...)`, `next_ip_prefix(pool, ...)` -> Record; `utilization(pool)`, `allocated(pool)` -> plain data. `from_pool` already passes through create() dicts. |
| Diff | `ih.diff`: `tree(branch, ...)`, `summary(branch, ...)` (GraphQL DiffTree/DiffTreeSummary), `files(branch)`, `artifacts(branch)` (REST). Reports return plain dicts: Records are for nodes, not reports. |
| Graph traversal | `ih.graph`: `paths(...)`, `path_exists(...)`, `reachable_nodes(...)` thin GraphQL wrappers (server >= 1.10; document). |
| Search | `ih.search(q)` -> lazy async iterator of brief Records via InfrahubSearchAnywhere. |
| Object type conversion | `ih.convert_object_type(id, target_kind, fields_mapping)`. |
| Object storage | `ih.storage`: `get(identifier) -> bytes`, `upload(content) -> {identifier, checksum}`, `upload_file(path)` (multipart), `get_file(node_id \| storage_id= \| kind= + hfid=) -> bytes` (CoreFileObject downloads; uploads deferred). |
| Artifacts | `ih.artifacts`: `fetch(artifact_id) -> bytes`, `generate(definition_id, nodes=None)`. |
| Transforms | `ih.transforms`: `render_python(id, branch=, at=, **params) -> Any`, `render_jinja2(id, ...) -> str`. |
| Stored queries | `ih.graphql.stored(query_id_or_name, variables=, branch=, at=, update_group=, subscribers=) -> GraphQLRecord`. |
| Schema load/check/converge | Api methods beside `schema()`: `load_schemas(schemas, branch=)`, `check_schemas(schemas, branch=)`, `schema_in_sync()`, `wait_schemas_converged(timeout=)` polling InfrahubStatus. |
| Compatibility matrix | Not enforced at runtime (the SDK does not either); `version()` exists for callers that gate. |

### Rejected as paradigm violations (permanent, with rationale)

- **Sync client**: house rule, no sync facade.
- **Store / identity map**: hidden cross-read state; every read here is an
  explicit request. (The SDK populates its store by default on every read.)
- **Batch executor**: asyncio.gather + Semaphore is the idiom; README gets a
  recipe instead of an abstraction.
- **Recorder/playback**: pass a custom httpx client / MockTransport.
- **CLI, pytest plugin, checks/generators/transform authoring, YAML spec
  loader, bulk transfer tooling**: infrahubctl and the SDK serve those; this
  is a client library. Repository *nodes* are ordinary kinds already.
- **Git operations (dulwich)**: violates httpx-only.
- **Proxy/TLS/requester config knobs**: `client=` takes a configured
  httpx.AsyncClient; that is the whole knob.
- **Per-kind protocol codegen**: our hints generator covers kind and filter
  completion; Records stay dynamic like the sisters'.

### Implementation waves (sequential, review gate between each)

- **Wave A (core-touching)**: JWT auth; `properties=True` + `record.meta()`;
  `record.fetch(rel)`; add_related/remove_related; `ih.tasks` +
  TaskTimeoutError; branches wait=False task tie-in; `ih.search`;
  convert_object_type.
- **Wave B (managers)**: `ih.pools`, `ih.diff`, `ih.graph`, `ih.storage`,
  `ih.artifacts`, `ih.transforms`, `graphql.stored()`, schema
  load/check/converge.
- **Wave C**: docs sweep (README/AGENTS/CHANGELOG/CONTRIBUTING), examples,
  fake extensions consolidation check, live sandbox validation, review,
  commits.

Status: Waves A and B are implemented, tested, and committed. The mapping
table above stays as the record of what each capability was resolved to; where
the shipped code and a row disagree, the code is the truth (storage's file
downloads, for one, landed as `get_file()` / `get_file_by_storage_id()` /
`get_file_by_hfid()` rather than one overloaded method).

## Open questions resolved during design

- **Wrappers: flatten vs preserve?** Flattened (decisions 5/6). The official
  SDK preserves wrappers as Attribute objects; the sisters' users expect
  `device.name == "x"`. Metadata access returns later behind an opt-in that
  hydrates wrappers as Records, which is additive, not breaking.
- **Retry on POST?** Yes for GraphQL queries (decision 10): they are reads.
  The GET-only rule is a REST-ism the sisters needed because method implied
  semantics; here the caller knows.
- **Raise on 200-with-errors?** Kind layer: yes (`GraphQLError`), because
  `get()`/`all()` have no partial-data story. Raw layer: no, house semantics
  (partial data is often still useful).
- **`is_visible`?** Does not exist on current Infrahub (removed; only
  `is_protected` remains as a flag property). Nothing in this client may
  reference it.
