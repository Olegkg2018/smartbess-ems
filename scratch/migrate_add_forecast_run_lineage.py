"""
Одноразова адитивна міграція: додає nullable-колонку forecast_run_id
(FK -> forecast_runs.id) до charge_discharge_plans і market_bids —
лінідж прогноз -> план -> заявка (CODE_REVIEW.md п.7-20, 2026-08-25,
CLAUDE.md п.37). У проєкті немає Alembic (Base.metadata.create_all()
створює лише НОВІ таблиці, не додає колонки в існуючі) — тому окремий
скрипт, за зразком scratch/migrate_market_price_kyiv_utc.py.

Ідемпотентно (перевіряє існування колонки перед ALTER) і працює як на
локальній SQLite, так і на Postgres в контейнері — обидва діалекти
приймають той самий синтаксис ALTER TABLE ... ADD COLUMN ... REFERENCES.

Запуск: python3 scratch/migrate_add_forecast_run_lineage.py [--apply]
Без --apply — dry-run (тільки друкує стан колонок, нічого не пише).
"""
import sys

sys.path.insert(0, '.')

from sqlalchemy import text
from src.database.session import SessionLocal

TABLES = ['charge_discharge_plans', 'market_bids']
COLUMN = 'forecast_run_id'


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
        for table in TABLES:
            exists = column_exists(db, table, COLUMN)
            print(f"{table}.{COLUMN} exists: {exists}")
            if not exists:
                to_alter.append(table)

        if not to_alter:
            print("Nothing to migrate — column already present on all tables.")
            return

        if not apply:
            print(f"\nDRY RUN — would ALTER: {to_alter}. Re-run with --apply to commit.")
            return

        for table in to_alter:
            print(f"Altering {table}...")
            db.execute(text(
                f"ALTER TABLE {table} ADD COLUMN {COLUMN} VARCHAR(36) REFERENCES forecast_runs(id)"
            ))
        db.commit()
        print("Applied.")

        for table in TABLES:
            assert column_exists(db, table, COLUMN), f"{table}.{COLUMN} missing after ALTER — investigate"
        print("Verified: column present on all tables.")
    finally:
        db.close()


if __name__ == '__main__':
    main()
