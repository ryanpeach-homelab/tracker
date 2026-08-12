export DATABASE_URI := env_var_or_default("DATABASE_URI", "postgresql://tracking:password@127.0.0.1:35432/tracking")

# Start Postgres (PostGIS) and wait until it's healthy.
up:
    docker compose up -d --wait

# Stop Postgres, keeping the data volume.
down:
    docker compose down

# Bring Postgres up, then apply all pending migrations.
migrate: up
    uv run alembic upgrade head

# Autogenerate a new revision from model changes. Usage: just revision "add users table"
revision message: migrate
    uv run alembic revision --autogenerate -m "{{message}}"

# Downgrade one revision (or pass a target: `just downgrade base`).
downgrade target="-1":
    uv run alembic downgrade {{target}}

# Show the current revision applied to the database.
current:
    uv run alembic current
