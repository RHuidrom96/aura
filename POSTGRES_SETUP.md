# PostgreSQL Setup Guide

This project has been migrated from **SQLite** to **PostgreSQL**.

## Prerequisites

* PostgreSQL installed and running
* Python dependencies installed:

```bash
pip install -r requirements.txt
```

---

## 1. Create a PostgreSQL Database

Create a new PostgreSQL database and user (if needed).

Example:

```sql
CREATE DATABASE your_database_name;
```

---

## 2. Configure Environment Variables

Create or update your `.env` file:

```env
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
POSTGRES_DB=your_database_name
POSTGRES_USER=your_username
POSTGRES_PASSWORD=your_password
```

---

## 3. Apply Database Schema

Run the Alembic migrations:

```bash
flask db upgrade
```

This creates the PostgreSQL tables.

---

## 4. Migrate Existing SQLite Data (Optional)

If you have existing data in `data/app.db`, migrate it into PostgreSQL:

```bash
python scripts/migrate.py
```

To preview the migration without writing data:

```bash
python scripts/migrate.py --dry-run
```

The migration script:

* Copies only matching columns.
* Preserves IDs and timestamps.
* Skips the `alembic_version` table.
* Ignores duplicate rows.
* Prints a migration summary when finished.

> **Note:** This step is only required when migrating data from an existing SQLite database. For a fresh installation, you can skip it.

---

## 5. Run the Application

```bash
python app.py
```

---

## Notes

* This guide is intended for **local development**.
* Do **not** commit your `.env` file.
* Ensure the PostgreSQL server is running before starting the application.
