import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from config.definitions import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, 'degiro.db')

# creating the engine does not open the database: it is opened at the first query
engine = create_engine(f'sqlite:///{DB_PATH}')
# sessions are opened per operation (with SessionLocal() as session: ...), never shared
SessionLocal = sessionmaker(bind=engine, autoflush=False)

Base = declarative_base()
