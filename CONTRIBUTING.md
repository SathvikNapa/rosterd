# Contributing to rosterd

Thanks for looking at this. A few things that'll make a contribution easy
to land.

## Local setup

Each Python service (`rosterd-kernel`, `rosterd-coordinator`,
`rosterd-ingestion`) is independently runnable:

```bash
cd rosterd-<service>
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # also installs libs/rosterd-contracts, editable
.venv/bin/pytest
./scripts/run.sh
```

The frontend:

```bash
cd rosterd-frontend
npm install
npm run dev
```

The whole stack, together:

```bash
docker compose up --build
```

See each service's own README for its specific environment variables and
what "real" vs. "simulated"/"scripted" mode means for it — several services
have a lower-dependency mode that needs no LLM key or Docker socket.

## Where new code belongs

Every Python service follows the same hexagonal layout (see the root
README's "Architecture" section for the full picture):

- **`domain/`** — pure logic, no I/O. Wire-contract Pydantic models and
  `ports.py` (the `Protocol` interfaces application code depends on) live
  here. If your change doesn't touch the network, a database, a subprocess,
  Docker, or an LLM provider, it almost certainly belongs in `domain/`.
- **`application/`** — orchestration and use cases. Depends on
  `domain/ports.py`, never on a concrete adapter module directly.
- **`adapters/`** — concrete implementations of a port: an HTTP client, a
  Postgres writer, a Docker backend, an LLM call. New adapters implement
  an existing `domain/ports.py` Protocol wherever one exists, rather than
  introducing a new one-off interface.
- **`main.py`** — thin entrypoint only. If you're adding real logic here,
  it probably belongs one layer down.

A few places intentionally don't follow this split perfectly (documented
inline with `# NOTE: pragmatic hexagonal exception` where they occur) —
untangling them would be a behavior change, not a structural one, and was
deliberately deferred rather than rushed.

## Tests

Every Python service's suite runs against fakes/stubs, not live
infrastructure — no Docker daemon, Postgres, or network access required
to run `pytest`. Please add a test with any behavior change; the existing
suites (`tests/`) are the actual safety net for a change like a
restructuring or a schema fix, not just documentation.

## Pull requests

- Keep a PR scoped to one change. A mechanical restructuring and a behavior
  fix are easier to review (and revert, if needed) as separate PRs.
- Run the affected service's test suite before opening the PR.
- If you're touching `libs/rosterd-contracts`, re-run every service's suite
  that imports it — it's the one place a change ripples across all four
  Python services at once.

## Security

`rosterd-ingestion` executes a target repo's own code during discovery
(subprocess-isolated, timeout-bounded, no container/seccomp separation yet
— see its README's "Security note"). If you find a way to escape that
isolation, please open a private report rather than a public issue.
