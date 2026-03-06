from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

from config.definitions import DATA_DIR


engine = create_engine(f'sqlite:///{DATA_DIR}/degiro.db', echo=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()
conn = SessionLocal()
