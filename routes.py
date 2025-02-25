from flask import Flask, request, jsonify, render_template
from openai import OpenAI
import json
import random
from datetime import datetime
from database import Base, db
from sqlalchemy import func, case

app = Flask(__name__)
client = OpenAI()

class ScienceQuizBot:
    def __init__(self):
        self.assistant = client.beta.assistants.create(
            name="중학교 과학 선생님",
            instructions="""
            당신은 중등 과학 과목에 대한 교육 전문가입니다...
            [여기에 전체 시스템 지침 입력]
            """,
            tools=[{"type": "retrieval"}],
            model="gpt-4-turbo-preview"
        )
        
    def create_thread(self):
        return client.beta.threads.create()
    
    def get_quiz(self, thread_id):
        message = client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content="새로운 문제를 출제해주세요."
        )
        
        run = client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=self.assistant.id
        )
        
        # 실행 완료 대기
        while run.status != 'completed':
            run = client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run.id
            )
        
        # 응답 가져오기
        messages = client.beta.threads.messages.list(thread_id=thread_id)
        return json.loads(messages.data[0].content[0].text.value)

    def check_answer(self, thread_id, user_answer):
        message = client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=f"정답은 {user_answer}입니다."
        )
        
        # 실행 및 응답 처리 로직
        # ...

@app.route('/api/quiz/new', methods=['POST'])
def new_quiz():
    quiz_bot = ScienceQuizBot()
    thread = quiz_bot.create_thread()
    quiz_data = quiz_bot.get_quiz(thread.id)
    
    return jsonify({
        'thread_id': thread.id,
        'quiz': quiz_data
    })

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

@app.route('/')
def quiz_page():
    return render_template('quiz.html') 