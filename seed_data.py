from app import app, db
from models import User, Answer
import random
from datetime import datetime, timedelta
from flask import Flask
from database import init_db
import os

def create_seed_data():
    with app.app_context():
        # 기존 데이터 삭제
        Answer.query.delete()
        User.query.delete()
        db.session.commit()

        # 단원 목록
        units = [
            "지권의 변화",
            "여러가지 힘",
            "생물의 다양성",
            "기권과 날씨",
            "빛과 파동"
        ]

        # 사용자 생성
        users = []
        for i in range(10):  # 10명의 학생
            user = User(username=f"student_{i+1}")
            users.append(user)
            db.session.add(user)
        
        db.session.commit()

        # 답변 생성
        for user in users:
            # 각 학생당 10-30개의 답변
            for _ in range(random.randint(10, 30)):
                unit = random.choice(units)
                is_correct = random.choice([True, False])
                created_at = datetime.utcnow() - timedelta(days=random.randint(0, 30))
                
                answer = Answer(
                    user_id=user.id,
                    unit=unit,
                    question="샘플 문제입니다.",
                    user_answer=str(random.randint(1, 5)),
                    is_correct=is_correct,
                    created_at=created_at
                )
                db.session.add(answer)

        db.session.commit()
        print("더미 데이터가 성공적으로 생성되었습니다.")

def create_app():
    app = Flask(__name__)
    init_db(app)
    return app

def seed_admin():
    app = create_app()
    
    with app.app_context():
        # 관리자 계정이 없는 경우에만 생성
        if not User.query.filter_by(username='admin').first():
            admin = User(username='admin')
            admin.set_password('admin123')
            db.session.add(admin)
            db.session.commit()
            print("관리자 계정이 생성되었습니다.")
        else:
            print("관리자 계정이 이미 존재합니다.")

if __name__ == "__main__":
    create_seed_data()
    seed_admin() 