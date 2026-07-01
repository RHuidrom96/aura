import os
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)


class Config:

    SECRET_KEY = os.getenv("MT_EVAL_SECRET_KEY")

    SQLALCHEMY_DATABASE_URI = (
        os.getenv("DATABASE_URL")
        or f"postgresql+psycopg2://"
           f"{os.getenv('POSTGRES_USER')}:"
           f"{os.getenv('POSTGRES_PASSWORD')}@"
           f"{os.getenv('POSTGRES_HOST')}:"
           f"{os.getenv('POSTGRES_PORT')}/"
           f"{os.getenv('POSTGRES_DB')}"
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    MAX_CONTENT_LENGTH = 16 * 1024 * 1024

    PERMANENT_SESSION_LIFETIME = timedelta(days=30)

    SESSION_REFRESH_EACH_REQUEST = True

    SESSION_COOKIE_HTTPONLY = True

    SESSION_COOKIE_SAMESITE = "Lax"