import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from src.core.config import settings

DATABASE_URL = settings.DATABASE_URL

if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

# 2026-09-28: ендпоінти тепер синхронні й виконуються паралельно в threadpool
# FastAPI (раніше async-обробники йшли по черзі в event loop) — зміна дати
# в UI шле ~10 запитів одночасно, плюс SCADA-потік і планувальник. Дефолтний
# пул (5+10) міг змусити запити чекати з'єднання; pre_ping — щоб не ловити
# "server closed the connection" після простою/рестарту Postgres.
pool_kwargs = {} if DATABASE_URL.startswith("sqlite") else {"pool_size": 10, "max_overflow": 20, "pool_pre_ping": True}

engine = create_engine(DATABASE_URL, connect_args=connect_args, **pool_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
