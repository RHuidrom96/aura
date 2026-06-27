#!/usr/bin/env python3
"""
SQLite to PostgreSQL migration script.

Features
--------
Discovers tables automatically
Copies only matching columns
Preserves IDs
Preserves timestamps
Works for future tables
Skips Alembic tables
Dry-run support
Progress summary
"""

import argparse
import os

from sqlalchemy import create_engine, MetaData, Table, select, inspect
from sqlalchemy.exc import IntegrityError
from dotenv import load_dotenv

load_dotenv()

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SQLITE_URL = f"sqlite:///{os.path.join(ROOT,'data','app.db')}"

POSTGRES_URL = (
    os.getenv("DATABASE_URL")
    or (
        f"postgresql+psycopg2://"
        f"{os.getenv('POSTGRES_USER')}:"
        f"{os.getenv('POSTGRES_PASSWORD')}@"
        f"{os.getenv('POSTGRES_HOST')}:"
        f"{os.getenv('POSTGRES_PORT')}/"
        f"{os.getenv('POSTGRES_DB')}"
    )
)

def reflect(engine):
    meta = MetaData()
    meta.reflect(bind=engine)
    return meta


def migrate(dry_run=False):

    sqlite_engine = create_engine(SQLITE_URL)
    postgres_engine = create_engine(POSTGRES_URL)

    sqlite_meta = reflect(sqlite_engine)
    postgres_meta = reflect(postgres_engine)

    sqlite_tables = set(sqlite_meta.tables.keys())
    postgres_tables = set(postgres_meta.tables.keys())

    tables = sorted(sqlite_tables & postgres_tables)

    print(f"\nFound {len(tables)} common tables.\n")

    summary = []

    with sqlite_engine.connect() as src_conn:
        with postgres_engine.begin() as dst_conn:

            for table_name in tables:

                if table_name == "alembic_version":
                    continue

                src_table = sqlite_meta.tables[table_name]
                dst_table = postgres_meta.tables[table_name]

                src_cols = set(src_table.columns.keys())
                dst_cols = set(dst_table.columns.keys())

                common_cols = [
                    c.name
                    for c in src_table.columns
                    if c.name in dst_cols
                ]

                rows = src_conn.execute(
                    select(*[src_table.c[c] for c in common_cols])
                ).mappings().all()

                copied = 0
                skipped = 0

                for row in rows:

                    values = dict(row)

                    if dry_run:
                        copied += 1
                        continue

                    try:
                        dst_conn.execute(
                            dst_table.insert().values(**values)
                        )
                        copied += 1

                    except IntegrityError:
                        skipped += 1

                summary.append(
                    (table_name, copied, skipped)
                )

                print(
                    f"✓ {table_name:<20}"
                    f" copied={copied:<6}"
                    f" skipped={skipped}"
                )

    print("\nMigration complete.\n")

    print("Summary")
    print("-" * 45)

    total = 0

    for table, copied, skipped in summary:

        total += copied

        print(
            f"{table:<20}"
            f"{copied:>8} copied"
            f"{skipped:>8} skipped"
        )

    print("-" * 45)
    print(f"Total rows copied: {total}")


if __name__ == "__main__":

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be copied."
    )

    args = parser.parse_args()

    migrate(dry_run=args.dry_run)
