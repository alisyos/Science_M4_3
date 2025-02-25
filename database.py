from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, scoped_session

# SQLite 데이터베이스 사용
engine = create_engine('sqlite:///quiz.db')

# 세션 생성
db_session = scoped_session(sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
))

# Base 클래스 생성
Base = declarative_base()
Base.query = db_session.query_property()

def init_db():
    # 데이터베이스 테이블 생성
    Base.metadata.create_all(bind=engine)

# 전역 db 객체 생성
db = db_session 