import os
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import declarative_base, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def get_data_dir() -> Path:
    configured_path = os.environ.get("DATA_DIR", "data")
    path = Path(configured_path).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path.resolve()


def get_database_path(data_dir: Path | None = None) -> Path:
    return (data_dir or get_data_dir()) / "shorts.db"


def get_database_url(data_dir: Path | None = None) -> str:
    return f"sqlite:///{get_database_path(data_dir).as_posix()}"


def create_database_engine(database_url: str | None = None):
    url = database_url or get_database_url()
    if url.startswith("sqlite:///"):
        database_path = Path(url.removeprefix("sqlite:///"))
        if not database_path.is_absolute():
            database_path = PROJECT_ROOT / database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        url,
        connect_args={"check_same_thread": False} if url.startswith("sqlite") else {},
    )

    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def configure_sqlite(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()

    return engine


DATABASE_URL = get_database_url()
engine = create_database_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
