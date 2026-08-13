# tracker

> This project is entirely vibe coded.

A minimal [FastMCP](https://github.com/jlowin/fastmcp) server for logging and querying measurements in PostgreSQL.

## Schema

```
tracking_key  (name TEXT PK, unit → tracking_unit, description TEXT NULL,
               frequency_unit TEXT NULL, frequency_count INT NULL)
tracking_unit (name TEXT PK, description TEXT NULL, json_schema JSONB NULL)
tracking      (id SERIAL PK, key → tracking_key, value FLOAT, description TEXT NULL,
               location geography(Point,4326) NULL, created_at TIMESTAMPTZ, metadata JSONB)
```

Keys and units each carry an optional free-form `description` of what they measure. An individual measurement also carries its own optional `description` — a per-reading note, distinct from the structured `metadata` JSONB.

Keys and units must be registered before use. `insert` enforces this at the application level; foreign keys enforce it at the database level.

Key/unit name formats (dot-separated snake_case keys, snake_case units) are validated on the ORM models themselves via SQLAlchemy `@validates`, so every write path — MCP tools or direct ORM use — is checked, not just the tool entrypoints.

A key can carry an optional **tracking frequency** — how often the measurement is meant to be recorded — stored as a `(frequency_unit, frequency_count)` pair (`day`/`week`/`month` × a positive integer). This captures the parametric "every n weeks" case exactly, without the month-vs-30-days lossiness of a raw interval; `daily`/`weekly`/`monthly` are just count 1, and "n weekly" is `('week', n)`. A `CHECK` constraint keeps the pair both-set-or-both-null. Friendly strings (`'daily'`, `'weekly'`, `'monthly'`, `'every 2 weeks'`, `'3 weekly'`) are converted by `parse_frequency`/`format_frequency` in `tracker_mcp.models`, and `TrackingKey.frequency` renders the label back.

A unit can carry an optional **metadata `json_schema`** — a [JSON Schema](https://json-schema.org/) (Draft 2020-12) that validates the `metadata` of every measurement recorded against that unit. The schema is checked for well-formedness when stored (via `validate_json_schema`), and each `insert`/`insert_batch`/`update_item` validates the measurement's metadata against it (via `validate_metadata`) — both in `tracker_mcp.models`, so every write path is covered. A `NULL` schema imposes no constraint; a `NULL` metadata is validated as an empty object so `required` fields are still enforced. `get_schema` reports every unit's schema.

`location` is an optional [PostGIS](https://postgis.net/) `geography(Point,4326)` — a WGS 84 geocoordinate for where a measurement was taken (requires the `postgis` extension, which the migration enables). Build one with `make_point(latitude, longitude)` from `tracker_mcp.models`, which validates the coordinate ranges; `insert` takes plain `latitude`/`longitude` decimal degrees.

## Tools

| Tool | Description |
|------|-------------|
| `get_version()` | Report the running server version and whether a newer release is available |
| `get_schema()` | Report the table columns and each unit's metadata JSON Schema |
| `new_key(name, unit, frequency?, description?)` | Register a measurement key, optionally with a tracking frequency (`daily`/`weekly`/`monthly`/`n weekly`) and a description |
| `update_key(name, unit?, frequency?, description?)` | Update a key's fields (omitted = untouched; `''` clears frequency/description; unit can't be cleared) |
| `rename_key(old_name, new_name)` | Rename a key, repointing its measurements |
| `new_unit(name, description?, json_schema?)` | Register a measurement unit, optionally with a description and a metadata JSON Schema |
| `update_unit(name, description?, json_schema?)` | Update a unit's fields (omitted = untouched; `''` clears the description, `{}` clears the schema) |
| `rename_unit(old_name, new_name)` | Rename a unit, repointing its measurements |
| `list_keys()` | List all registered keys |
| `list_units()` | List all registered units |
| `insert(key, value, latitude?, longitude?, meta?, description?)` | Append a measurement row, optionally with a per-reading description |
| `insert_batch(measurements, latitude?, longitude?, meta?)` | Append many rows sharing one location, timestamp, and metadata (each measurement may carry its own description) |
| `update_item(id, key?, value?, latitude?, longitude?, meta?, description?)` | Update fields of an existing measurement by `id` (only provided fields change; `''` clears the description) |
| `delete_item(id)` | Delete a measurement by `id` |
| `query(sql)` | Read-only `SELECT` against the tracking database |

## Setup

```sh
uv sync
export DATABASE_URI=postgresql://tracking:password@127.0.0.1:5432/tracking
uv run alembic upgrade head   # create / migrate the schema
uv run tracker
```

The schema is managed by [Alembic](https://alembic.sqlalchemy.org/). Run
`uv run alembic upgrade head` to bring a database up to the latest schema before
starting the server. Alembic reads the same `DATABASE_URI` environment variable.

The database server must have PostGIS available; the migration runs
`CREATE EXTENSION IF NOT EXISTS postgis`, so the connecting role needs
permission to create the extension (or an admin can create it beforehand). The
[`postgis/postgis`](https://hub.docker.com/r/postgis/postgis) Docker image is an
easy way to get this.

If you have an existing database created by an earlier version (via
`SQLModel.metadata.create_all`, before Alembic was added), stamp it at the
baseline first so migrations don't try to recreate existing tables:

```sh
uv run alembic stamp 0001_initial_schema
uv run alembic upgrade head
```

## Releases

Versioning is automatic and driven by pull request labels — you never edit a
version by hand, and there is no version number committed to the repo. **Git
tags are the single source of truth.** The `get_version` tool reports the
running server's version (from the `TRACKER_VERSION` env var baked into the image
at build time) and, when the GitHub Releases API is reachable, compares it
against the latest published release so you can tell whether the server is up to
date:

```
tracker 0.2.0
latest release: 0.2.0 — up to date
```

If GitHub can't be reached, the current version is still returned with a note
that the check was skipped. A fork can point the check at its own releases by
setting `TRACKER_GITHUB_REPO=owner/repo` (default `ryanpeach-homelab/tracker`);
`TRACKER_RELEASE_CHECK_TIMEOUT` (seconds, default `5`) bounds the API call.

### How a release is cut

Every PR must carry exactly one **version-bump label** that says how the release
version should change when it merges (following [semver](https://semver.org/)):

| Label   | Bump          | Use for |
|---------|---------------|---------|
| `major` | `x.0.0`       | breaking changes |
| `minor` | `0.x.0`       | new, backward-compatible features |
| `patch` | `0.0.x`       | bug fixes and internal changes |

The **PR Labels** workflow (`.github/workflows/pr-labels.yml`) fails the PR
until exactly one of these labels is set, so nothing merges without declaring
its bump. On merge into the default branch, the **Release** workflow
(`.github/workflows/release.yml`) then:

1. computes the next version from the latest `v*` tag and the PR's bump label;
2. pushes the new `vX.Y.Z` tag (nothing else is written to the repo);
3. builds the container image from the `Dockerfile`, passing the version as the
   `VERSION` build arg (which becomes `TRACKER_VERSION` in the image), and pushes
   it to GHCR as `ghcr.io/ryanpeach-homelab/tracker:vX.Y.Z` and `:latest`;
4. creates a GitHub Release with auto-generated notes — this is the release
   `get_version` compares against.

Because the release only pushes a **tag**, it needs no write access to the
default branch — branch protection ("changes must go through a PR") can stay
fully locked with no bypass. The three labels must exist in the repository
(create `major`, `minor`, and `patch` once under **Issues → Labels**), and you
can make **PR Labels** a required status check so an unlabeled PR can't merge.

The version lives only in tags: `pyproject.toml` declares `dynamic = ["version"]`
and the build reads a throwaway placeholder from `tracker_mcp.__version__` just
to satisfy packaging. Neither is the running version — that is always
`TRACKER_VERSION`.

### Deploying a release

Deploy the published image (supply `DATABASE_URI`, and `NTFY_URL` if you want
the reminder loop). Run migrations with the same image before starting the
server:

```sh
docker run --rm -e DATABASE_URI=... ghcr.io/ryanpeach-homelab/tracker:latest \
  alembic upgrade head
docker run -e DATABASE_URI=... ghcr.io/ryanpeach-homelab/tracker:latest
```

## Development

```sh
uv sync --group dev
pre-commit install
pre-commit run --all-files   # ruff + basedpyright
```

To change the schema, edit the models in `src/tracker_mcp/models.py`, then
autogenerate a migration:

```sh
uv run alembic revision --autogenerate -m "describe the change"
```
