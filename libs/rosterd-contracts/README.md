# rosterd-contracts

Shared wire-contract types used by every rosterd service: `rosterd-kernel`,
`rosterd-coordinator`, `rosterd-ingestion`, `rosterd-demo-agent`.

Until this package existed, these six types (`Priority`, `RunStatus`,
`SiteStatus`, `Violation`, `GraphEdge`, `GraphSpec`) lived as a byte-for-byte
duplicated `shared.py` in all four services — correct in principle (each
service stays independently deployable, no shared runtime dependency to
version-lock across services) but wrong in practice: four copies that had to
be kept in sync by hand, with no mechanism catching drift.

This package is that mechanism. It's a real, versioned, pip-installable
package rather than a `PYTHONPATH` trick or a symlink, specifically so it
works identically in a bare local `.venv` and inside each service's Docker
build, and so a breaking contract change can be versioned and adopted by
each service on its own schedule rather than silently drifting.

## Install

Each service installs this as an editable local dependency in development
(`pip install -e ../libs/rosterd-contracts`) and as a normal wheel in its
Docker build (see each service's `Dockerfile`).

## What's here

`src/rosterd_contracts/types.py` — the six types, unchanged in shape from
the original `shared.py`. `__init__.py` re-exports them so every service
keeps writing `from rosterd_contracts import RunStatus` rather than reaching
into the `types` submodule directly.
