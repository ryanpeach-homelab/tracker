# tracker

> This project is entirely vibe coded.

A minimal [FastMCP](https://github.com/jlowin/fastmcp) server for logging and querying measurements in PostgreSQL.

## Schema

```
tracking_key  (name TEXT PK)
tracking_unit (name TEXT PK)
tracking      (id SERIAL PK, key → tracking_key, value FLOAT, unit → tracking_unit,
               location geography(Point,4326) NULL, created_at TIMESTAMPTZ, metadata JSONB)
```

Keys and units must be registered before use. `insert` enforces this at the application level; foreign keys enforce it at the database level.

Key/unit name formats (dot-separated snake_case keys, snake_case units) are validated on the ORM models themselves via SQLAlchemy `@validates`, so every write path — MCP tools or direct ORM use — is checked, not just the tool entrypoints.

`location` is an optional [PostGIS](https://postgis.net/) `geography(Point,4326)` — a WGS 84 geocoordinate for where a measurement was taken (requires the `postgis` extension, which the migration enables). Build one with `make_point(latitude, longitude)` from `tracker_mcp.models`, which validates the coordinate ranges; `insert` takes plain `latitude`/`longitude` decimal degrees.

## Tools

| Tool | Description |
|------|-------------|
| `new_key(name)` | Register a measurement key |
| `rename_key(old_name, new_name)` | Rename a key, repointing its measurements |
| `new_unit(name)` | Register a measurement unit |
| `list_keys()` | List all registered keys |
| `list_units()` | List all registered units |
| `insert(key, value, unit, latitude?, longitude?, meta?)` | Append a measurement row |
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
