# tracker

> This project is entirely vibe coded.

A minimal [FastMCP](https://github.com/jlowin/fastmcp) server for logging and querying measurements in PostgreSQL.

## Schema

```
tracking_key  (name TEXT PK)
tracking_unit (name TEXT PK)
tracking      (id SERIAL PK, key → tracking_key, value FLOAT, unit → tracking_unit,
               created_at TIMESTAMPTZ, metadata JSONB)
```

Keys and units must be registered before use. `insert` enforces this at the application level; foreign keys enforce it at the database level.

## Tools

| Tool | Description |
|------|-------------|
| `new_key(name)` | Register a measurement key |
| `rename_key(old_name, new_name)` | Rename a key, repointing its measurements |
| `new_unit(name)` | Register a measurement unit |
| `list_keys()` | List all registered keys |
| `list_units()` | List all registered units |
| `insert(key, value, unit, meta?)` | Append a measurement row |
| `query(sql)` | Read-only `SELECT` against the tracking database |

## Setup

```sh
uv sync
DATABASE_URI=postgresql://tracking:password@127.0.0.1:5432/tracking uv run tracker
```

Tables are created automatically on first start via `SQLModel.metadata.create_all`.

## Development

```sh
uv sync --group dev
pre-commit install
pre-commit run --all-files   # ruff + basedpyright
```
