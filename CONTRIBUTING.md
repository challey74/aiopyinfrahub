# Contributing

## Development setup

The project is managed with [uv](https://docs.astral.sh/uv/) and requires
Python 3.11+:

```sh
uv sync              # install the environment
uv run pytest        # run the test suite
uv run ruff check    # lint
uv run ruff format   # format
uv run pyright       # type check
```

All four checks run in CI and must pass.

## Testing conventions

Tests run entirely against `FakeInfrahub` in `tests/conftest.py`, an in-memory
Infrahub served through `httpx2.MockTransport`. It answers `GET /api/schema` and
`GET /api/info`, the auth routes with expirable JWTs, and the storage,
artifact, transform, stored-query, schema load/check and diff REST routes, and
it executes enough of the GraphQL surface to round-trip reads, mutations,
pagination, branch operations, tasks, pools, diffs, graph traversal, and
search. Tests never touch the network and never require a real Infrahub
instance. If your change needs a route, a kind, or a behavior the fake doesn't
model yet, extend the fake rather than reaching for a mocking library.

New features and bug fixes should come with tests. A test that encodes server
behavior should say so in its docstring, e.g. "Infrahub answers HTTP 200 with
an `errors` array when execution fails."

`tests/test_demo_integration.py` is the one exception, and it is opt-in: the
module is skipped unless `AIOPYINFRAHUB_DEMO_URL` is set, so CI and the
default run stay offline. Reads work anonymously against
https://sandbox.infrahub.app; the write tests need `AIOPYINFRAHUB_DEMO_WRITES=1`
and `AIOPYINFRAHUB_DEMO_TOKEN` on a disposable instance. That sandbox has no
published reset schedule, so never assert an exact object count or a
dataset-specific name there, and keep the request volume low.

## Generated files

`src/aiopyinfrahub/kinds_generated.py` and
`src/aiopyinfrahub/hints_generated.pyi` are generated from a live Infrahub
schema and must not be edited by hand. Change the generator instead and
regenerate:

```sh
uv run scripts/generate_kinds.py      # defaults to sandbox.infrahub.app
uv run ruff format src/aiopyinfrahub/kinds_generated.py src/aiopyinfrahub/hints_generated.pyi
```

A weekly workflow does the same and opens a PR, so a hand edit would be
silently reverted.

## Dependencies

httpx2 is the only runtime dependency and that is a hard constraint, not a
preference. The dev group is exactly four tools (pyright, pytest,
pytest-asyncio, ruff). Proposals that add a runtime dependency need a very
strong case; the usual answer is to hand-roll the small piece we need, which is
why retries and the GraphQL renderer are written out rather than pulled in.

## Design constraints

This library deliberately differs from both the official `infrahub-sdk` and
the sync pynetbox/pynautobot lineage: all I/O is explicit and awaitable.
Before proposing API changes, read the design constraints in
[AGENTS.md](AGENTS.md), particularly the list of behaviors that must not be
replicated (lazy attribute and relationship fetches, `len()` that does I/O,
properties that make requests, an implicit identity map). [PLAN.md](PLAN.md)
records every resolved decision and the reasoning behind it.

The GraphQL renderer is the injection guard: kind names, field names, and
filter keys are validated as identifiers before they are rendered. Never build
query text with f-strings at a call site.

No code from infrahub-sdk, pynautobot, or pynetbox is vendored here. If that
ever changes, the vendored file must keep its original copyright notice and
Apache License 2.0 header, and [NOTICE](NOTICE) must be updated.

## Commits and pull requests

- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `ci:`, `chore:`, ...).
- Keep changes focused; unrelated refactoring belongs in its own PR.
- User-visible changes get a line in `CHANGELOG.md` under Unreleased.

## Releases

1. Bump `version` in `pyproject.toml` and move the Unreleased entries into a
   new version section in `CHANGELOG.md`.
2. Tag `v<version>` and publish a GitHub release.

The release workflow asserts that the tag matches the project version before
building, and publishes to PyPI through trusted publishing (no token secret).
