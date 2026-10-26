from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Kita hardcode URL SQLite di sini untuk tahap awal, 
# nanti bisa dipindahkan ke config/settings.yaml jika diperlukan.
DATABASE_URL = "sqlite:///data/shorts.db"

# engine dengan check_same_thread=False penting untuk SQLite di FastAPI/Worker
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()