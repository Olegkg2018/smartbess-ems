"""
Одноразова адитивна міграція: додає nullable-колонки external_order_id/
oree_submission_status/submitted_at до market_bids — емуляція подачі
заявки на біржу через MockOreeClient ("віртуальний диспетчер",
2026-08-26, CLAUDE.md). За зразком scratch/migrate_add_forecast_run_lineage.py
(немає Alembic у проєкті, create_all() не додає колонки в існуючі таблиці).

Ідемпотентно, працює і на локальній SQLite, і на Postgres в контейнері.

Запуск: python3 scratch/migrate_add_bid_submission_fields.py [--apply]
Без --apply — dry-run.
"""
import sys

sys.path.insert(0, '.')

from sqlalchemy import text
from src.database.session import SessionLocal

TABLE = 'market_bids'
COLUMNS = {
    'external_order_id': 'VARCHAR(64)',
    'oree_submission_status': 'VARCHAR(20)',
    'submitted_at': 'TIMESTAMP',
}


def column_exists(db, table: str, column: str) -> bool:
    dialect = db.bind.dialect.name
    if dialect == 'sqlite':
        rows = db.execute(text(f"PRAGMA table_info({table})")).fetchall()
        return any(r[1] == column for r in rows)
    rows = db.execute(text(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = :table AND column_name = :column"
    ), {'table': table, 'column': column}).fetchall()
    return len(rows) > 0


def main():
    apply = '--apply' in sys.argv
    db = SessionLocal()
    try:
        dialect = db.bind.dialect.name
        print(f"Dialect: {dialect}")
        to_alter = []
        for column in COLUMNS:
            exists = column_exists(db, TABLE, column)
            print(f"{TABLE}.{column} exists: {exists}")
            if not exists:
                to_alter.append(column)

        if not to_alter:
            print("Nothing to migrate — all columns already present.")
            return

        if not apply:
            print(f"\nDRY RUN — would ALTER {TABLE}, add: {to_alter}. Re-run with --apply to commit.")
            return

        for column in to_alter:
            print(f"Altering {TABLE}: add {column} {COLUMNS[column]}...")
            db.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {column} {COLUMNS[column]}"))
        db.commit()
        print("Applied.")

        for column in COLUMNS:
            assert column_exists(db, TABLE, column), f"{TABLE}.{column} missing after ALTER — investigate"
        print("Verified: all columns present.")
    finally:
        db.close()


if __name__ == '__main__':
    main()
