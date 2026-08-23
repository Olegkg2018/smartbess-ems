"""
Одноразова міграція market_prices: старий naive-Kyiv режим (timestamp
напряму = сира київська година з таблиці oree, БЕЗ конвертації в UTC) ->
новий, правильний, справжній-UTC режим (той самий, що вже застосовується
в fetch_oree_market_month з 2026-08-21 і реально пише в БД з 2026-08-23
00:00:00 — цей момент емпірично підтверджений порядковим порівнянням
кожного рядка проти живого oree-фетчу, а не взятий з дати коміту коду).

CLAUDE.md хронологія п.26/27 — контекст і повний розбір бага.

Запуск: python3 scratch/migrate_market_price_kyiv_utc.py [--apply]
Без --apply — dry-run (тільки друкує статистику й перевірки, нічого не пише).
"""
import sys
import datetime

sys.path.insert(0, '.')

from src.database.session import SessionLocal
from src.database.models import MarketPrice
from src.core.time_utils import kyiv_to_utc

CUTOVER = datetime.datetime(2026, 8, 23, 0, 0)


def main():
    apply = '--apply' in sys.argv
    db = SessionLocal()
    try:
        old_rows = db.query(MarketPrice).filter(MarketPrice.timestamp < CUTOVER).order_by(MarketPrice.timestamp).all()
        print(f"Rows in old (naive-Kyiv) regime, timestamp < {CUTOVER}: {len(old_rows)}")
        if not old_rows:
            print("Nothing to migrate.")
            return

        corrections = []
        for r in old_rows:
            date_str = r.timestamp.strftime('%Y-%m-%d')
            hour = r.timestamp.hour
            new_ts = kyiv_to_utc(date_str, hour)
            corrections.append((r, new_ts))

        # Весняний перехід EET->EEST (остання неділя березня) — київська
        # 03:00 фізично НЕ існує того дня (годинник одразу стрибає на
        # 04:00), але таблиця oree все одно має 24 колонки — колонки
        # "3" і "4" localize'яться (nonexistent='shift_forward', той
        # самий policy, що вже в fetch_oree_market_month) в ОДНУ й ту ж
        # реальну мить. Чесно (не вигадуємо третє значення): лишаємо
        # рядок з ПІЗНІШОЮ вихідною годиною (вона реально відбулась),
        # прибираємо рядок з годиною, якої того дня не було.
        from collections import defaultdict
        by_new_ts = defaultdict(list)
        for r, new_ts in corrections:
            by_new_ts[new_ts].append((r, new_ts))
        deduped = []
        n_dropped = 0
        for new_ts, group in by_new_ts.items():
            if len(group) > 1:
                group.sort(key=lambda x: x[0].timestamp)
                kept = group[-1]
                dropped = group[:-1]
                print(f"DST spring-forward collision at {new_ts}: keeping original hour {kept[0].timestamp}, dropping nonexistent hour(s) {[d[0].timestamp for d in dropped]}")
                n_dropped += len(dropped)
                deduped.append(kept)
            else:
                deduped.append(group[0])
        corrections = deduped
        print(f"Dropped {n_dropped} nonexistent-hour rows (DST spring-forward), {len(corrections)} rows remain to migrate.")

        new_ts_list = [c[1] for c in corrections]
        assert len(new_ts_list) == len(set(new_ts_list)), "duplicate corrected timestamps remain after DST dedup — investigate before applying"

        max_new_ts = max(new_ts_list)
        print(f"Max corrected timestamp: {max_new_ts} (must stay < {CUTOVER})")
        assert max_new_ts < CUTOVER, "a corrected old-regime timestamp lands at/after the cutover — theory violated, investigate before applying"

        existing_new_regime = {
            r[0] for r in db.query(MarketPrice.timestamp).filter(MarketPrice.timestamp >= CUTOVER).all()
        }
        collide_with_new_regime = set(new_ts_list) & existing_new_regime
        print(f"Collisions with already-correct (new-regime) rows: {len(collide_with_new_regime)}")
        assert not collide_with_new_regime, "corrected timestamps collide with existing new-regime rows — investigate before applying"

        sample = corrections[:3] + corrections[-3:]
        print("Sample corrections (old naive-Kyiv -> new genuine-UTC):")
        for r, new_ts in sample:
            print(f"  {r.timestamp} (price {r.price_uah}) -> {new_ts}")

        if not apply:
            print("\nDRY RUN — no changes written. Re-run with --apply to commit.")
            return

        print("\nApplying migration (delete old-timestamp rows, insert corrected rows in one transaction)...")
        new_objects = []
        for r, new_ts in corrections:
            new_objects.append(MarketPrice(
                timestamp=new_ts, price_uah=r.price_uah, price_eur=r.price_eur,
                volume_mwh=r.volume_mwh, area=r.area,
            ))
        db.query(MarketPrice).filter(MarketPrice.timestamp < CUTOVER).delete(synchronize_session=False)
        db.flush()
        db.bulk_save_objects(new_objects)
        db.commit()

        total_after = db.query(MarketPrice).count()
        print(f"Done. Total market_prices rows after migration: {total_after}")
    finally:
        db.close()


if __name__ == '__main__':
    main()
