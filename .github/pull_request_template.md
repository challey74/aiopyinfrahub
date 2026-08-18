## What does this change?

<!-- A sentence or two. Link the issue if there is one. -->

## Why?

<!-- What problem does it solve, or what Infrahub behavior does it match? -->

## Checklist

- [ ] `uv run pytest` passes
- [ ] `uv run ruff check` and `uv run ruff format --check` pass
- [ ] `uv run pyright` passes
- [ ] New behavior has tests, extending `FakeInfrahub` in `tests/conftest.py` if needed
- [ ] User-visible changes have a `CHANGELOG.md` entry under Unreleased
- [ ] No new runtime dependency (httpx2 is the only one)
- [ ] If this changes the async API surface, it does not reintroduce implicit I/O (see the design constraints in `AGENTS.md`)
- [ ] Any new GraphQL text goes through the renderer, not f-strings
