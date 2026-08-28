"""
Одноразова адитивна міграція: додає nullable-колонку idm_bid_price_uah до
market_bids — ціна, яку диспетчер свідомо обрав подати на ВДР
(submit_single_idm_fallback_bid, "подача заявки на ВДР з можливістю
скоригувати ціну", 2026-08-28). За зразком
scratch/migrate_add_idm_price_is_actual_field.py.

Запуск: python3 scratch/migrate_add_idm_bid_price_field.py [--apply]
Без --apply — dry-run.
"""
import sys

sys.path.insert(0, '.')

from sqlalchemy import text
from src.database.session import SessionLocal

TABLE = 'market_bids'
COLUMNS = {
    'idm_bid_price_uah': 'FLOAT',
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
