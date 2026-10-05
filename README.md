# rosterd

**Contract-enforced orchestration for AI agents.** rosterd reads an agent's
own code, turns it into an enforced contract (who can call what, at what
limits, gated behind whose approval), and kills any dispatch that breaks
it - at the moment the tool call happens, not after the fact.

If an agent tried something it shouldn't, right now, what actually stops
it? That's the problem this exists to answer.

## How it fits together

Four independently-deployable services plus a shared Postgres schema for
live cross-service state. There's no built-in reference agent service in
this repo - rosterd governs whatever agent repo you point `rosterd-ingestion`
at.

| Service | What it does | Docs |
|---|---|---|
| [`rosterd-ingestion`](rosterd-ingestion/) | Clones a real agent repo, statically discovers its graph + tool schemas, merges in human-reviewed business limits, and produces a confirmed manifest - the contract everything else enforces. | [README](rosterd-ingestion/README.md) |
| [`rosterd-kernel`](rosterd-kernel/) | One per site. Dispatches tasks to the governed agent, checks every tool call against the confirmed manifest, kills on a breach, autoscales per agent under load. | [README](rosterd-kernel/README.md) |
| [`rosterd-coordinator`](rosterd-coordinator/) | Watches every site's kernel for a shared violation pattern and pushes a tightened policy to every *other* site automatically - the cross-site half of enforcement - and relays live Postgres updates to the frontend over a WebSocket. | [README](rosterd-coordinator/README.md) |
| [`rosterd-frontend`](rosterd-frontend/) | React + Vite UI across the whole flow: ingest → review → ask → schedule → run detail → monitor. | [README](rosterd-frontend/README.md) |
| [`rosterd-postgres`](rosterd-postgres/) | The shared schema every service's dashboard reads from in real time - agents, metrics, sites, events, tasks, manifests - with a trigger that NOTIFYs on every write. | [README](rosterd-postgres/README.md) |

Each service also builds and tests standalone - nothing here requires the
whole stack running to work on one piece of it.

## Architecture

Every Python service follows the same shape:

```
rosterd-<service>/
├── domain/         # pure business logic + wire-contract models + ports.py
│                    # (the interfaces application code depends on, never a
│                    #  concrete adapter)
├── application/     # orchestration / use cases, depends on domain/ports.py
├── adapters/        # concrete implementations of those ports: HTTP in/out,
│                    # Docker, Postgres, an LLM provider, the filesystem -
│                    # split into one subpackage per thing being adapted to
└── main.py          # thin entrypoint wiring adapters to application
```

Swapping what a service talks to (a simulated Docker backend for a real one,
a logging state writer for a live Postgres one, a scripted agent brain for
a real LLM call) means writing a new adapter against an existing port -
never touching domain or application code. Several of these seams
(`DockerBackend`, `StateWriter`, `ScriptedBrain`/`LLMBrain`) already
existed informally before this layout made them explicit.

The one deliberately shared piece is [`libs/rosterd-contracts`](libs/rosterd-contracts/)
- the wire-contract types (`Priority`, `RunStatus`, `Violation`, `GraphSpec`,
…) every service's domain layer depends on, as one real installed package
instead of four hand-copied files.

## Prerequisites

- [Docker](https://docs.docker.com/get-docker/) ≥ 24 and Docker Compose V2
- [Python](https://www.python.org/) ≥ 3.11 (for running services standalone or tests)
- [Node.js](https://nodejs.org/) ≥ 20 and npm (for the frontend)

## Quick start

```bash
docker compose up --build
```

Then:

```bash
open http://localhost:16686                          # Jaeger - full request traces
curl  http://localhost:8100/healthz                   # kernel
curl  http://localhost:8300/healthz                   # coordinator
psql postgresql://rosterd:rosterd@localhost:5432/rosterd -c "SELECT * FROM agents"
```

Open `http://localhost:5173` for the UI. See each service's own README for
running it standalone (every one has its own `scripts/run.sh` and test
suite - `pytest` for the four Python services, `npm run typecheck && npm
run build` for the frontend).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for local setup, the test workflow,
and where new code belongs under the hexagonal layout above. This project
follows the [Contributor Covenant](CODE_OF_CONDUCT.md).

## Contributors

<!-- Add yourself here! Format: [Name](GitHub profile) -->
- [Sathvik Napa](https://github.com/SathvikNapa) - contributor
- [Param Chawla](https://github.com/anonymous2912) - contributor
- [Shruti Patki](https://github.com/ShrutiPatki) - contributor

## License

Apache License 2.0 - see [LICENSE](LICENSE). Copyright 2026 Sathvik Napa
and contributors.
