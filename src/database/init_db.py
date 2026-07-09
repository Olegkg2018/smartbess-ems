from src.database.session import engine, Base
from sqlalchemy import text

def init_db():
    print("Initializing database tables...")
    Base.metadata.create_all(bind=engine)
    
    # Try to convert tables to TimescaleDB hypertables if running on PostgreSQL
    db_type = engine.name
    if db_type == "postgresql":
        print("PostgreSQL detected, checking for TimescaleDB extension...")
        with engine.connect() as conn:
            try:
                # Enable timescaledb extension if not enabled
                conn.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"))
                conn.commit()
                print("TimescaleDB extension verified/enabled.")
                
                # Check and create hypertables
                hypertables = [
                    ("market_prices", "timestamp"),
                    ("price_forecasts", "timestamp"),
                    ("bess_telemetry", "timestamp"),
                    ("charge_discharge_plans", "timestamp"),
                    ("manual_overrides", "timestamp")
                ]
                
                for table, time_col in hypertables:
                    try:
                        # Check if already a hypertable
                        res = conn.execute(text(f"SELECT * FROM timescaledb_information.hypertables WHERE hypertable_name = '{table}';"))
                        if not res.fetchone():
                            print(f"Converting table '{table}' to hypertable on column '{time_col}'...")
                            conn.execute(text(f"SELECT create_hypertable('{table}', '{time_col}', if_not_exists => TRUE);"))
                            conn.commit()
                        else:
                            print(f"Table '{table}' is already a hypertable.")
                    except Exception as he:
                        print(f"Warning: could not convert table '{table}' to hypertable: {he}")
                        conn.rollback()
            except Exception as e:
                print(f"TimescaleDB initialization skipped or not supported: {e}")
                
    print("Database initialization complete.")

if __name__ == "__main__":
    init_db()
