# PostgreSQL Setup Guide

This project uses **PostgreSQL** and **Alembic** for database migrations.

## Prerequisites

- PostgreSQL installed and running
- Python dependencies installed

```bash
pip install -r requirements.txt
```

---

## 1. Configure Environment Variables

Create a `.env` file:

```env
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=your_database_name
POSTGRES_USER=your_username
POSTGRES_PASSWORD=your_password
```

---

## 2. Create the Database

### PostgreSQL CLI

Using the PostgreSQL CLI:

```bash
psql -U your_username -d your_database_name
```

```bash
createdb your_database_name
```

### SQL

```sql
CREATE DATABASE your_database_name;
```

---

## 3. Fresh Installation

```bash
flask db upgrade
python app.py
```

---

## 4. After Pulling New Changes

Always run:

```bash
git pull
flask db upgrade
```

This applies any new database migrations (e.g. new columns such as `email_verified`).

---

## 5. Reset Local Development Database (Optional)

If your local database becomes inconsistent during development:

### PostgreSQL CLI

```bash
dropdb your_database_name
createdb your_database_name
```

### SQL

```sql
DROP DATABASE your_database_name;
CREATE DATABASE your_database_name;
```

Then recreate the schema:

```bash
flask db upgrade
python app.py
```

> **Warning:** This permanently deletes all local development data.

---

## 6. Migrate Existing SQLite Data (Optional)

Only required when migrating data from the old SQLite database.

```bash
python scripts/migrate.py
```

Preview without writing:

```bash
python scripts/migrate.py --dry-run
```

---

## Notes

- Never commit your `.env` file.
- Always run `flask db upgrade` after pulling the latest changes.
- Use `flask db migrate` **only** when creating a new migration.
- Use `flask db upgrade` to apply existing migrations.