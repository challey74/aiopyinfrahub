# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-08-18

### Changed

- **Breaking**: the runtime dependency is now
  [httpx2](https://github.com/pydantic/httpx2) rather than httpx, picked up
  after upstream activity on httpx stalled. httpx2 is still the *only* runtime
  dependency and the client's own API is unchanged, but two things cross the
  boundary: a custom client passed via `client=` must now be an
  `httpx2.AsyncClient`, and a connection failure surfaces httpx2's transport
  exceptions (`httpx2.TransportError` and its subclasses) rather than httpx's.
  A caching transport built for httpx, hishel's among them, will not accept an
  httpx2 client.

## [0.1.0] - 2026-08-18

Initial release. Targets Infrahub 1.3+, written against 1.10.x behavior.

### Added

- Fully async `Api` client on httpx, usable as an async context manager, with
  httpx as the only runtime dependency. A client passed via `client=` stays
  open on close (httpx convention); the Api closes only clients it creates.
- `X-INFRAHUB-KEY: <token>` authentication and a
  `python-aiopyinfrahub/<version>` User-Agent. `token=None` omits the header,
  since Infrahub allows anonymous reads by default.
- Schema-kind attribute access: `ih.InfraDevice` resolves any kind from
  `GET /api/schema` rather than a fixed app/endpoint tree, because Infrahub's
  object model is instance-defined. `ih.kind("InfraDevice")` is the escape
  hatch for kinds held in strings, and an unknown kind fails on its first
  awaited operation with a message listing near-miss kinds.
- Per-branch schema cache behind a single lock, so concurrent first calls make
  one fetch; `await ih.schema(branch=..., refresh=True)` forces a refetch.
  Nodes, generics, profiles, and templates flatten into one `{kind: schema}`
  map keyed by `namespace + name`.
- `get()` / `filter()` / `all()` / `count()` / `create()` / `upsert()` on every
  kind. `get(pk)` accepts a UUID or, for kinds whose schema defines one, a
  `default_filter` value; `get(hfid=[...])` works wherever the schema defines
  `human_friendly_id`. `get(**filters)` returns `None` on no match and raises
  `ValueError` on more than one, so a "unique lookup" never silently picks the
  first row.
- Lazy async result sets: nothing is fetched until iteration starts, and after
  page 1 reveals `count` the remaining offsets are fetched through a sliding
  window bounded by `max_concurrency` and yielded in offset order. Breaking out
  of an iteration early cancels the in-flight fetches instead of buffering
  pages you will never read.
- Attribute and relationship flattening at parse: `{"value": v}` becomes the
  bare value and `{"node": ...}` / `{"count": n, "edges": [...]}` become a
  brief `Record` and a `list[Record]`, so reads are `device.name` and
  `device.site.display_label` rather than `.value` / `.peer` chains. The Record
  remembers which keys were wrapped and rebuilds the wire shape on serialize.
- Diff-based `Record.save()`, which sends `<Kind>Update` carrying only the
  fields that actually changed, so a partial edit does not clobber concurrent
  changes to other fields. Plus `delete()` (`<Kind>Delete`) and explicit
  `full_details()` for brief nested records, which re-queries the record's own
  branch.
- Schema-driven input shaping on `create()` / `upsert()`: scalars are wrapped
  as `{"value": v}`, relationship ids as `{"id": v}` or `[{"id": ...}]`, and a
  dict or list of dicts always passes through untouched, so the full wire shape
  is always available when the shorthand is not enough.
- Default field selection standing in for GraphQL's missing `SELECT *`: `id`,
  `hfid`, `display_label`, `__typename`, every attribute, and only those
  relationships the schema marks `Attribute` or `Parent`, so a list query does
  not fan out across every many-relationship. `include=` / `exclude=` adjust it
  per call.
- Branch support: `api(url, branch=...)` sets a client-wide default and every
  read, `graphql.query()`, and `schema()` takes a per-call `branch=` override.
  GraphQL requests carry the branch in the URL path (Infrahub has no `branch`
  query parameter there); REST requests carry it as `?branch=`.
- Time travel via `at=` on read operations and `graphql.query()`, letting you
  read the graph as of a timestamp. Mutations deliberately do not accept it,
  because the server resets it.
- Branch management through `ih.branches`: `list()`, `create()`, `delete()`,
  `update()`, `rebase()`, `merge()`, and `validate()`, wrapping Infrahub's
  hand-written mutations including the flat (non-paginated) `Branch` list
  query and the `wait_until_completion` argument.
- Raw GraphQL as a first-class path, not a fallback:
  `await ih.graphql.query(query, variables)` returns a `GraphQLRecord` whose
  `.data` and `.errors` are both reachable, so a 200 response carrying partial
  data plus errors stays usable.
- A dict-to-GraphQL renderer with no parser dependency, which validates kind
  names, field names, and filter keys as identifiers before rendering and
  inlines strings through `json.dumps`. This closes the injection surface
  without needing variable promotion.
- Automatic retries with capped exponential backoff and jitter, keyed on
  idempotency rather than HTTP method: every node read is a POST to `/graphql`,
  so a GET-only rule would never retry a read. 429 is retried for everything
  honoring `Retry-After` (capped at 60s); 502/503/504 and transport failures
  are retried only for idempotent requests, since an ambiguous write may
  already have been processed. Configurable via `Api(retries=)`, default 3.
- Flat exception taxonomy: `RequestError` for non-success HTTP,
  `ContentError` for a successful response whose body is not JSON, and
  `GraphQLError` for a body carrying an `errors` array. The kind layer raises
  on 200-with-errors because `get()` and `all()` have no partial-data story;
  the raw GraphQL layer does not, because partial data is often still useful.
- Record equality and hashing by Infrahub identity, `(branch, id)`. There is no
  hyperlinked `url` field to key on, and the same id on two branches is two
  different states of the object.
- `register_model(kind, record_class)` to map a kind to your own `Record`
  subclass.
- `await ih.version()` from `GET /api/info`.
- JWT authentication as the alternative to `token=`:
  `api(url, username=..., password=...)` logs in on the first request that
  needs credentials, single-flight behind a lock so concurrent first requests
  produce one session rather than one each, sends `Authorization: Bearer`, and
  recovers from a 401 by refreshing and replaying once, or by logging in again
  when the refresh token has expired too. `await ih.login()` / `logout()` drive
  it explicitly. The two schemes are mutually exclusive in the constructor,
  because Infrahub resolves an API key before a JWT and would otherwise ignore
  the credentials you passed.
- `properties=True` on `get()` / `filter()` / `all()`, which selects attribute
  and relationship metadata (`is_protected`, `updated_at`, `source`, `owner`,
  plus `is_default`, which only attributes carry) and hands it back through
  `record.meta(name)`. Reads stay flattened either way, and metadata is never
  serialized, diffed, or saved, so opting in changes nothing about how a record
  writes.
- `await record.fetch(rel_name)`, which hydrates one relationship the default
  selection leaves out and merges it in. This is the explicit answer to
  infrahub-sdk's `prefetch_relationships`: one call, one query, nothing fetched
  behind your back. Only that key of the diff snapshot is refreshed, so an edit
  pending elsewhere on the record survives.
- `await record.add_related(name, peers)` and `remove_related(name, peers)`
  over RelationshipAdd / RelationshipRemove, taking peers as ids, Records, or
  RelatedNodeInput dicts. Both answer with `ok` alone, so re-read or `fetch()`
  the relationship to see the result.
- `ih.tasks` over Infrahub's server-side task queue: lazy `all()` / `filter()`,
  `get(id)`, `count()`, and `wait(id, timeout=, interval=)`, which polls until
  a task leaves the active states and otherwise raises the new
  `TaskTimeoutError`. Branch `create()` / `delete()` / `rebase()` / `merge()` /
  `validate()` called with `wait=False` now return the queued task's id to feed
  it, since the mutation's `object` at that point describes work not yet done.
- `ih.pools`: `next_ip_address()` and `next_ip_prefix()` allocate from a
  resource pool and return the allocation as a brief Record; `identifier=`
  makes a repeat call hand back the same resource instead of consuming another,
  which is what makes an allocation safe to re-run. `utilization()` and
  `allocated()` report on a pool as plain data.
- `ih.diff`: `tree()` and `summary()` over DiffTree / DiffTreeSummary, plus
  `files()` and `artifacts()` over the REST diff routes. All four return plain
  dicts, because a diff is a report and Records are for nodes.
- `ih.graph`: `paths()`, `path_exists()`, and `reachable_nodes()` over
  InfrahubPathTraversal / InfrahubReachableNodes. These need Infrahub 1.10+;
  an older server answers with a GraphQL error naming the unknown field.
- `ih.storage`: `get()`, `upload()`, `upload_file()`, and the CoreFileObject
  reads `get_file()`, `get_file_by_storage_id()`, and `get_file_by_hfid()`,
  all returning bytes rather than decoded JSON, because a stored object is a
  file and the server answers with whatever type it was stored under.
- `ih.artifacts`: `fetch()` for an artifact's content and `generate()` to queue
  an artifact definition's artifacts. Artifact nodes themselves stay an
  ordinary kind (`ih.CoreArtifact`).
- `ih.transforms`: `render_python()`, which decodes the data structure a Python
  transform returns, and `render_jinja2()`, which returns the rendered text
  the route answers with. Extra keyword arguments become the transform's own
  GraphQL variables.
- `await ih.graphql.stored(id_or_name, variables=...)` for queries stored on
  the server, with `update_group=` and `subscribers=`. It is the one GraphQL
  call served over a REST route, so it takes the branch as a query parameter
  rather than as a URL path suffix.
- Schema management beside `schema()`: `load_schemas()`, `check_schemas()` (a
  dry run that writes nothing), `schema_in_sync()`, and
  `wait_schemas_converged()`, which polls InfrahubStatus and raises the new
  `ConvergenceTimeoutError`. A load returns as soon as the schema is stored and
  the workers adopt it on their own schedule, so convergence is a separate
  wait; a load also drops that branch's cached schema rather than refetching
  it, so the next read pays for a request only if there is one.
- `ih.search(q)`, a lazy iterator of brief Records across every kind via
  InfrahubSearchAnywhere, and
  `ih.convert_object_type(id, target_kind, fields_mapping)` for the conversion
  mutation.
- Two renderer additions carrying the above: `EnumValue`, a `str` subclass
  rendered as a bare enum token because graphene rejects a quoted string where
  an enum is declared (`InfrahubTask(state:)`, a diff's `status`), still
  validated as an identifier so it opens no injection hole; and `segment()`,
  which percent-encodes every caller value that lands in a URL path, since
  httpx escapes query parameters but leaves the path alone.
- Full type hints with a `py.typed` marker, plus generated kind hints so
  `ih.InfraDevice` autocompletes: a bare-annotations mixin
  (`kinds_generated.py`) that `Api` inherits and a stub-only
  `hints_generated.pyi`, both produced by `scripts/generate_kinds.py` from a
  live `GET /api/schema`. Run it with `--url` / `--token` to regenerate them
  from your own instance's schema. Unknown kinds stay legal at runtime and for
  the type checker, so hints are never a restriction.
- Opt-in live integration tests (`tests/test_demo_integration.py`), skipped
  unless `AIOPYINFRAHUB_DEMO_URL` is set. Reads run anonymously against
  https://sandbox.infrahub.app; the write tests additionally need
  `AIOPYINFRAHUB_DEMO_WRITES=1` and a token, and clean up after themselves.
- A weekly workflow that regenerates the kind hints from the public sandbox
  and opens a pull request when the schema has moved.
- A runnable FastAPI example (`examples/fastapi_app.py`) showing the
  app-state / lifespan usage pattern for long-lived services.

[unreleased]: https://github.com/challey74/aiopyinfrahub/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/challey74/aiopyinfrahub/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/challey74/aiopyinfrahub/releases/tag/v0.1.0
