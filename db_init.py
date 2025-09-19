# db_init.py
from sqlalchemy import create_engine
from models import Base
import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("Set DATABASE_URL in .env")

engine = create_engine(DATABASE_URL, echo=True, future=True)
print("Creating all tables...")
Base.metadata.create_all(bind=engine)
print("Done.")
