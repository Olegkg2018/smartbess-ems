"""
Одноразова адитивна міграція: додає nullable-колонку
idm_fallback_price_is_actual до market_bids — "Загальний дохід" у звіті
(2026-08-28), розрізняє ОЦІНКУ ВДР-фолбека (settle_bids_for_date) від
РЕАЛЬНОЇ звіреної ціни (reconcile_idm_fallback_for_date). За зразком
scratch/migrate_add_idm_fallback_fields.py.

Нова таблиця idm_prices (IdmPrice) НЕ потребує міграції — нова таблиця,
з'являється через звичайний create_all() при рестарті.

Запуск: python3 scratch/migrate_add_idm_price_is_actual_field.py [--apply]
Без --apply — dry-run.
"""
import sys

sys.path.insert(0, '.')

from sqlalchemy import text
from src.database.session import SessionLocal

TABLE = 'market_bids'
COLUMNS = {
    'idm_fallback_price_is_actual': 'BOOLEAN',
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
