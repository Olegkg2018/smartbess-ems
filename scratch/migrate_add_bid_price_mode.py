"""
Одноразова адитивна міграція: market_bids.bid_price_mode (VARCHAR(20), nullable)
— як порахована ціна заявки: 'breakeven' (беззбиткова ціна, 2026-10-07) або
'margin' (прогноз ± буфер). NULL — історичні заявки (завжди буфер).

Запуск: python3 scratch/migrate_add_bid_price_mode.py [--apply]
Без --apply — dry-run.
"""
import sys

sys.path.insert(0, '.')

from sqlalchemy import text
from src.database.session import SessionLocal

TABLES_COLUMNS = {
    'market_bids': {'bid_price_mode': 'VARCHAR(20)'},
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
        for table, columns in TABLES_COLUMNS.items():
            for column, col_type in columns.items():
                exists = column_exists(db, table, column)
                print(f"{table}.{column} exists: {exists}")
                if not exists:
                    to_alter.append((table, column, col_type))

        if not to_alter:
            print("Nothing to migrate — all columns already present.")
            return

        if not apply:
            print(f"\nDRY RUN — would ALTER: {to_alter}. Re-run with --apply to commit.")
            return

        for table, column, col_type in to_alter:
            print(f"Altering {table}: add {column} {col_type}...")
            db.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
        db.commit()
        print("Applied.")

        for table, columns in TABLES_COLUMNS.items():
            for column in columns:
                assert column_exists(db, table, column), f"{table}.{column} missing after ALTER — investigate"
        print("Verified: all columns present.")
    finally:
        db.close()


if __name__ == '__main__':
    main()
