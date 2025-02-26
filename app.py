from flask import Flask, request, jsonify, render_template, session, redirect, url_for, flash
from openai import OpenAI
import json
import random
from datetime import datetime
from models import db, User, Answer
from sqlalchemy import func, case, distinct
from sqlalchemy.sql import expression
import time
from dotenv import load_dotenv
import os
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from flask_login import UserMixin

app = Flask(__name__)
load_dotenv()

# Flask 설정
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')  # 환경 변수에서 가져오거나 기본값 사용

# SQLAlchemy 설정
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///quiz.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db.init_app(app)

# Flask-Login 설정
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "이 페이지에 접근하려면 로그인이 필요합니다."

# 데이터베이스 테이블 생성
with app.app_context():
    db.drop_all()  # 기존 데이터베이스 삭제
    db.create_all()  # 새로운 데이터베이스 생성

# API 키 확인
api_key = os.getenv('OPENAI_API_KEY')
if not api_key:
    print("Error: API key not found in .env")
    raise ValueError("OpenAI API key not found")
else:
    print("API key loaded successfully:", api_key[:10] + "...")

# OpenAI 클라이언트 설정
client = OpenAI(
    api_key=api_key,
    base_url="https://api.openai.com/v1"  # 기본 OpenAI API 엔드포인트 사용
)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('quiz_page'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        if user and password == user.password:  # 실제 서비스에서는 비밀번호 해시를 비교해야 함
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('quiz_page'))
        else:
            flash('아이디 또는 비밀번호가 올바르지 않습니다.', 'error')
    
    return render_template('login.html')

class ScienceQuizBot:
    def __init__(self):
        self.assistant_id = os.getenv('ASSISTANT_ID')
        self.current_quiz = None  # 현재 퀴즈 정보 저장
        if not self.assistant_id:
            raise ValueError("Assistant ID not found in environment variables")

    def create_thread(self):
        try:
            thread = client.beta.threads.create()
            return thread.id
        except Exception as e:
            print(f"Error creating thread: {str(e)}")
            raise e

    def get_quiz(self, thread_id=None):
        try:
            if not thread_id:
                thread = client.beta.threads.create()
                thread_id = thread.id
                
            message = client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content="새로운 퀴즈를 출제해주세요."
            )
            
            run = client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=self.assistant_id
            )
            
            while True:
                run = client.beta.threads.runs.retrieve(
                    thread_id=thread_id,
                    run_id=run.id
                )
                if run.status == 'completed':
                    break
                time.sleep(0.5)
            
            response = client.beta.threads.messages.list(thread_id=thread_id)
            response_text = response.data[0].content[0].text.value
            
            print("\n=== 퀴즈 응답 ===")
            print(response_text)
            
            if '{' in response_text and '}' in response_text:
                json_text = response_text[response_text.find('{'):response_text.rfind('}')+1]
                result = json.loads(json_text)
                result['thread_id'] = thread_id
                if result.get('type') == 'QUIZ':
                    self.current_quiz = result.get('quiz')  # 퀴즈 정보 저장
                return result
            else:
                raise ValueError("JSON 형식의 응답을 찾을 수 없습니다.")
            
        except Exception as e:
            print(f"Error in get_quiz: {str(e)}")
            return {
                "error": "퀴즈를 생성하는 중 오류가 발생했습니다.",
                "details": str(e)
            }

    def check_answer(self, thread_id, user_answer):
        try:
            message = client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=f"사용자가 '{user_answer}'라고 답변했습니다."
            )
            
            run = client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=self.assistant_id
            )
            
            while True:
                run = client.beta.threads.runs.retrieve(
                    thread_id=thread_id,
                    run_id=run.id
                )
                if run.status == 'completed':
                    break
                time.sleep(0.5)
            
            response = client.beta.threads.messages.list(thread_id=thread_id)
            response_text = response.data[0].content[0].text.value
            
            print("\n=== 답변 평가 ===")
            print(response_text)
            
            if '{' in response_text and '}' in response_text:
                json_text = response_text[response_text.find('{'):response_text.rfind('}')+1]
                result = json.loads(json_text)
                
                # 퀴즈 정보 추가
                if result.get('type') == 'ANSWER':
                    result['quiz'] = self.current_quiz
                return result
            else:
                raise ValueError("JSON 형식의 응답을 찾을 수 없습니다.")
            
        except Exception as e:
            print(f"Error in check_answer: {str(e)}")
            return {
                "type": "ANSWER",
                "answer": {
                    "correct": False,
                    "explanation": str(e)
                }
            }

    def get_explanation(self, thread_id, question):
        try:
            message = client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=question
            )
            
            run = client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=self.assistant_id
            )
            
            while True:
                run = client.beta.threads.runs.retrieve(
                    thread_id=thread_id,
                    run_id=run.id
                )
                if run.status == 'completed':
                    break
                time.sleep(0.5)
            
            response = client.beta.threads.messages.list(thread_id=thread_id)
            response_text = response.data[0].content[0].text.value
            
            print("\n=== 일반 질문 답변 ===")
            print(response_text)
            
            # JSON 부분만 추출하고 파싱
            if '{' in response_text and '}' in response_text:
                json_text = response_text[response_text.find('{'):response_text.rfind('}')+1]
                result = json.loads(json_text)
                
                # CHAT 타입 응답 처리
                if result.get('type') == 'CHAT':
                    return result
                else:
                    return {
                        "type": "CHAT",
                        "message": result.get('message', result.get('explanation', response_text))
                    }
            else:
                # JSON이 없는 경우 텍스트 전체를 메시지로 사용
                return {
                    "type": "CHAT",
                    "message": response_text
                }
                
        except Exception as e:
            print(f"Error in get_explanation: {str(e)}")
            return {
                "type": "CHAT",
                "message": str(e)
            }

@app.route('/')
@login_required
def quiz_page():
    return render_template('quiz.html')

@app.route('/api/quiz/new', methods=['POST'])
def new_quiz():
    try:
        quiz_bot = ScienceQuizBot()
        # thread_id 없이 get_quiz 호출
        quiz_data = quiz_bot.get_quiz()
        return jsonify(quiz_data)
    except Exception as e:
        print(f"Error in new_quiz: {str(e)}")
        return jsonify({
            "error": "퀴즈를 생성하는 중 오류가 발생했습니다.",
            "details": str(e)
        }), 500

@app.route('/api/quiz/answer', methods=['POST'])
def submit_answer():
    data = request.json
    thread_id = data.get('thread_id')
    answer = data.get('answer')
    
    quiz_bot = ScienceQuizBot()
    result = quiz_bot.check_answer(thread_id, answer)
    
    return jsonify(result)

@app.route('/api/chat', methods=['POST'])
@login_required
def chat():
    try:
        data = request.get_json()
        thread_id = data.get('thread_id')
        message = data.get('message')
        
        if not thread_id or not message:
            return jsonify({"error": "thread_id와 message는 필수입니다."}), 400
            
        quiz_bot = ScienceQuizBot()
        
        if message.strip() == "테스트 시작":
            result = quiz_bot.get_quiz(thread_id)
            return jsonify(result)
            
        if message.strip() in ["①", "②", "③", "④", "⑤"] or message.strip() in ["1", "2", "3", "4", "5"]:
            result = quiz_bot.check_answer(thread_id, message)
            
            if (result.get('type') == 'ANSWER' and 
                'answer' in result and 
                'correct' in result['answer']):
                try:
                    # 현재 로그인한 사용자의 정보 사용
                    answer = Answer(
                        user_id=current_user.id,  # 실제 로그인한 사용자 ID
                        unit=quiz_bot.current_quiz.get('unit', '미분류') if quiz_bot.current_quiz else '미분류',
                        question=quiz_bot.current_quiz.get('question', '') if quiz_bot.current_quiz else '',
                        user_answer=message.strip(),
                        is_correct=result['answer']['correct']
                    )
                    db.session.add(answer)
                    db.session.commit()
                except Exception as e:
                    print(f"답변 저장 중 오류 발생: {str(e)}")
            
            return jsonify(result)
        
        result = quiz_bot.check_answer(thread_id, message)
        return jsonify(result)
        
    except Exception as e:
        print(f"Chat error: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/admin')
def admin_dashboard():
    total_students = User.query.count()
    total_answers = Answer.query.count()
    correct_answers = Answer.query.filter_by(is_correct=True).count()
    accuracy_rate = (correct_answers / total_answers * 100) if total_answers > 0 else 0
    
    # 단원별 통계
    unit_stats = db.session.query(
        Answer.unit,
        func.count(Answer.id).label('attempts'),
        func.sum(func.cast(Answer.is_correct, db.Integer)).label('correct'),
        func.count(distinct(Answer.user_id)).label('unique_students')
    ).group_by(Answer.unit).all()
    
    unit_stats = [{
        'name': unit or '미분류',
        'total_questions': 5,
        'attempts': attempts or 0,  # None 처리
        'correct': int(correct or 0),  # None 처리
        'accuracy_rate': (int(correct or 0)/(attempts or 1) * 100),  # 0으로 나누기 방지
        'unique_students': unique_students or 0  # None 처리
    } for unit, attempts, correct, unique_students in unit_stats]
    
    # 학생별 통계
    student_stats = db.session.query(
        User,
        func.coalesce(func.count(Answer.id), 0).label('total'),  # None 대신 0
        func.coalesce(func.sum(func.cast(Answer.is_correct, db.Integer)), 0).label('correct')  # None 대신 0
    ).join(Answer, isouter=True).group_by(User).all()
    
    # 전체 단원 수 계산
    total_units = db.session.query(func.count(distinct(Answer.unit))).scalar() or 0
    
    # 평균 진도율 계산
    if total_units > 0 and total_students > 0:
        progress_values = []
        for user in User.query.all():
            unique_units = db.session.query(func.count(distinct(Answer.unit))).filter(Answer.user_id == user.id).scalar() or 0
            progress = (unique_units / total_units * 100)
            progress_values.append(progress)
        average_progress = sum(progress_values) / len(progress_values) if progress_values else 0
    else:
        average_progress = 0
    
    print("Debug Info:")
    print(f"Total Students: {total_students}")
    print(f"Total Answers: {total_answers}")
    print(f"Correct Answers: {correct_answers}")
    print(f"Unit Stats: {unit_stats}")
    print(f"Student Stats: {student_stats}")
    
    return render_template('admin.html',
                         total_students=total_students,
                         total_answers=total_answers,
                         accuracy_rate=accuracy_rate,
                         average_progress=average_progress,
                         unit_stats=unit_stats,
                         student_stats=student_stats)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('로그아웃되었습니다.')
    return redirect(url_for('login'))

@app.route('/admin/users')
def user_management():
    users = User.query.all()
    return render_template('user_management.html', users=users)

@app.route('/admin/users/add', methods=['POST'])
def add_user():
    username = request.form.get('username')
    password = request.form.get('password')
    
    if User.query.filter_by(username=username).first():
        flash('이미 존재하는 사용자명입니다.', 'error')
        return redirect(url_for('user_management'))
    
    user = User(username=username, password=password)
    db.session.add(user)
    db.session.commit()
    
    flash('계정이 생성되었습니다.', 'success')
    return redirect(url_for('user_management'))

@app.route('/admin/users/edit', methods=['POST'])
def edit_user():
    user_id = request.form.get('user_id')
    username = request.form.get('username')
    password = request.form.get('password')
    
    user = User.query.get_or_404(user_id)
    
    if user.username != username and User.query.filter_by(username=username).first():
        flash('이미 존재하는 사용자명입니다.', 'error')
        return redirect(url_for('user_management'))
    
    user.username = username
    if password:  # 비밀번호가 입력된 경우에만 변경
        user.password = password
    
    db.session.commit()
    flash('계정이 수정되었습니다.', 'success')
    return redirect(url_for('user_management'))

@app.route('/admin/users/delete/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    
    flash('계정이 삭제되었습니다.', 'success')
    return redirect(url_for('user_management'))

if __name__ == '__main__':
    app.debug = True
    app.run()