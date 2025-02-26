from app import app
from models import db, User, Answer
import random
from datetime import datetime, timedelta

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

if __name__ == "__main__":
    create_seed_data() 