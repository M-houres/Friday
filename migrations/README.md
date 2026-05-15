Alembic migration skeleton for Friday.

Current status:

- `alembic.ini` is wired.
- `migrations/env.py` resolves the runtime database URL from `alembic.ini` or `src.config.settings`.
- `migrations/script.py.mako` is present for new revisions.
- `migrations/versions/` is ready for real revision files.

Recommended usage:

1. Keep `src/db_schema.py` only as a local bootstrap/dev fallback.
2. Generate a baseline revision from the current PostgreSQL schema.
3. Put every schema change into Alembic revisions after that.
4. Use Alembic for staging/production rollout and rollback.

Typical commands:

- `alembic revision -m "baseline"`
- `alembic upgrade head`
- `alembic downgrade -1`
