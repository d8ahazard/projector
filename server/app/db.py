from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from .config import DB_PATH

engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def init_db() -> None:
    from . import models  # noqa: F401
    import sqlite3
    from .config import WORK_DIR
    # sqlite backup includes WAL contents; never copy a live database file alone.
    with sqlite3.connect(DB_PATH) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > 5:
            raise RuntimeError("Database schema is newer than this server; use the matching server version")
        exists = db.execute("SELECT name FROM sqlite_master WHERE name='mediaasset'").fetchone()
        if version < 5 and exists:
            backup = WORK_DIR / "backups"
            backup.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(backup / f"projector-before-v5-{models.now_ms()}.db") as dest:
                db.backup(dest)
        if version < 2 and exists:
            cols = {r[1] for r in db.execute("PRAGMA table_info(mediaasset)")}
            for name, definition in {"source_file_id": "VARCHAR", "published": "BOOLEAN NOT NULL DEFAULT 1", "technical": "JSON NOT NULL DEFAULT '{}'"}.items():
                if name not in cols:
                    db.execute(f"ALTER TABLE mediaasset ADD COLUMN {name} {definition}")
            db.commit()
        device_exists = db.execute("SELECT name FROM sqlite_master WHERE name='device'").fetchone()
        if version < 4 and device_exists:
            cols = {r[1] for r in db.execute("PRAGMA table_info(device)")}
            if "cache_budget_mb" not in cols:
                db.execute("ALTER TABLE device ADD COLUMN cache_budget_mb INTEGER NOT NULL DEFAULT 1536")
            db.commit()
        if version < 2:
            for table, fields in {"generationproject":{"character_design":"VARCHAR NOT NULL DEFAULT ''", "orientation":"VARCHAR NOT NULL DEFAULT 'vertical'"}, "storyboardshot":{"background":"VARCHAR NOT NULL DEFAULT ''"}}.items():
                cols={r[1] for r in db.execute(f"PRAGMA table_info({table})")}
                if cols:
                    for name, definition in fields.items():
                        if name not in cols: db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            db.commit()
    SQLModel.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA user_version=5")


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
