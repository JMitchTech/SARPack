# SARPack — Database Migrations

Managed by [Alembic](https://alembic.sqlalchemy.org/).
Always run commands from the `SARPack/` root directory.

---

## Setup (first time on a new machine)

```cmd
pip install alembic sqlalchemy psycopg2-binary
```

Copy `.env.template` to `.env` and fill in your values before running
any migration commands.

---

## Common commands

### Apply all pending migrations (run this on every deploy)
```cmd
alembic upgrade head
```

### Check current schema version
```cmd
alembic current
```

### View full migration history
```cmd
alembic history --verbose
```

### Roll back one migration
```cmd
alembic downgrade -1
```

### Roll back to a specific version
```cmd
alembic downgrade 0001_baseline
```

---

## Adding a new migration (when the schema changes)

1. Make your schema change in `core/db.py` (add the new column/table to `SCHEMA_SQL`)
2. Generate a new migration file:
   ```cmd
   alembic revision -m "short description of change"
   ```
3. Open the generated file in `migrations/versions/` and write the
   `upgrade()` and `downgrade()` functions manually.
   Example — adding a column:
   ```python
   def upgrade():
       op.add_column("personnel",
           sa.Column("avatar_url", sa.Text, nullable=True)
       )

   def downgrade():
       op.drop_column("personnel", "avatar_url")
   ```
4. Test the migration:
   ```cmd
   alembic upgrade head
   alembic downgrade -1
   alembic upgrade head
   ```
5. Commit the migration file to git alongside your code change.

---

## Important rules

- **Never edit a migration that has already been applied** to any
  deployed database. Create a new migration instead.
- **Always write a `downgrade()`** even if you think you'll never
  use it. One bad deploy and you'll be glad it's there.
- **Migration files are committed to git.** The `.env` file is not.
- The baseline migration (`0001_baseline`) represents the full
  initial schema. Every subsequent migration is a delta on top of it.

---

## Migration file naming convention

```
NNNN_short_description.py
```

Examples:
- `0002_add_patient_forms.py`
- `0003_add_equipment_table.py`
- `0004_incident_add_priority_field.py`

The numeric prefix keeps migrations in order in the file listing.
Alembic uses the `revision` string inside the file, not the filename,
for actual ordering — but consistent naming makes the history readable.
