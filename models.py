from sqlalchemy import Column, Integer, String, Boolean, DateTime
from database import Base

class QuizResult(Base):
    __tablename__ = 'quiz_results'
    
    id = Column(Integer, primary_key=True)
    student_id = Column(Integer)
    unit = Column(String)
    question = Column(String)
    user_answer = Column(String)
    is_correct = Column(Boolean)
    created_at = Column(DateTime) 