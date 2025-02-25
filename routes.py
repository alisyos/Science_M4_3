from flask import Flask, request, jsonify, render_template
from openai import OpenAI
import json
import random
from datetime import datetime
from database import Base, db
from sqlalchemy import func, case
import os
import time

app = Flask(__name__)
client = OpenAI(
    api_key=os.getenv('OPENAI_API_KEY')
)

class ScienceQuizBot:
    def __init__(self):
        self.assistant_id = os.getenv('ASSISTANT_ID')
        if not self.assistant_id:
            raise ValueError("Assistant ID not found in environment variables")

    def get_quiz(self):
        try:
            print("Getting quiz...")
            # 직접 Assistant 호출
            run = client.beta.assistants.create(
                assistant_id=self.assistant_id,
                instructions="새로운 문제를 출제해주세요."
            )
            print("Run completed")
            
            return json.loads(run.content[0].text.value)
            
        except Exception as e:
            print(f"Error in get_quiz: {str(e)}")
            raise e

    def check_answer(self, thread_id, user_answer):
        message = client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=f"정답은 {user_answer}입니다."
        )
        
        # 실행 및 응답 처리 로직
        # ...

def init_routes(app):
    @app.route('/')
    def index():
        return render_template('quiz.html')

    @app.route('/api/quiz/new', methods=['POST'])
    def new_quiz():
        try:
            print("=== Starting new quiz request ===")
            quiz_bot = ScienceQuizBot()
            print("Quiz bot created")
            
            quiz_data = quiz_bot.get_quiz()
            print(f"Quiz data received")
            
            return jsonify({
                'quiz': quiz_data
            })
        except Exception as e:
            print(f"Error in new_quiz: {str(e)}")
            return jsonify({"error": str(e)}), 500

@app.route('/api/quiz/answer', methods=['POST'])
def submit_answer():
    data = request.json
    thread_id = data['thread_id']
    answer = data['answer']
    
    quiz_bot = ScienceQuizBot()
    result = quiz_bot.check_answer(thread_id, answer)
    
    # 결과 저장
    quiz_result = QuizResult(
        student_id=data['student_id'],
        unit=result['quiz']['unit'],
        question=result['quiz']['question'],
        user_answer=answer,
        is_correct=result['answer']['correct'],
        created_at=datetime.now()
    )
    db.session.add(quiz_result)
    db.session.commit()
    
    return jsonify(result)

@app.route('/api/admin/statistics', methods=['GET'])
def get_statistics():
    results = db.session.query(
        QuizResult.unit,
        func.count(QuizResult.id).label('total_questions'),
        func.sum(case((QuizResult.is_correct, 1), else_=0)).label('correct_answers')
    ).group_by(QuizResult.unit).all()
    
    return jsonify([{
        'unit': r.unit,
        'total': r.total_questions,
        'correct': r.correct_answers
    } for r in results]) 