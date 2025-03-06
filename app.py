from flask import Flask, request, jsonify, render_template, session, redirect, url_for, flash, make_response
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

# 환경 변수 로드
load_dotenv()

# Flask 앱 설정
app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

# Flask 설정
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev')

# PostgreSQL 설정
if os.environ.get('DATABASE_URL'):
    database_url = os.environ.get('DATABASE_URL')
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # 로컬 개발용 SQLite
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///quiz.db'

app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Flask-Login 설정
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "이 페이지에 접근하려면 로그인이 필요합니다."

# 데이터베이스 초기화
db.init_app(app)

# 앱 컨텍스트 내에서 데이터베이스 생성
with app.app_context():
    db.create_all()
    # 관리자 계정이 없는 경우 생성
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin')
        admin.set_password('admin123')
        db.session.add(admin)
        db.session.commit()

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

# 전역 변수로 current_quiz 저장
current_quiz_store = {}

# ScienceQuizBot 클래스 정의
class ScienceQuizBot:
    def __init__(self):
        self.assistant_id = os.getenv('ASSISTANT_ID')
        self.current_quiz = None
        if not self.assistant_id:
            raise ValueError("Assistant ID not found in environment variables")

    def get_quiz(self, thread_id, question_count=1):
        try:
            # 명확한 퀴즈 생성 요청
            prompt = f"""중학교 과학 관련 문제를 {question_count}개 출제해주세요.
반드시 다음 JSON 형식을 정확히 따라야 합니다:

{{
  "type": "QUIZ",
  "questions": [
    {{
      "unit": "단원명",
      "question": "문제 내용",
      "options": ["보기1", "보기2", "보기3", "보기4", "보기5"],
      "correct": "정답",
      "type": "용어 정의",
      "explanation": "해설"
    }}
  ]
}}

중요 규칙:
1. questions 배열에 정확히 {question_count}개의 문제가 있어야 합니다.
2. 각 문제는 반드시 unit, question, options, correct, type, explanation 필드를 모두 포함해야 합니다.
3. options 배열은 정확히 5개의 보기를 포함해야 합니다.
4. correct는 반드시 options 배열의 요소 중 하나와 정확히 일치해야 합니다."""

            print("=== 전송하는 프롬프트 ===")
            print(prompt)
            print("========================")

            client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=prompt
            )
            
            run = client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=self.assistant_id
            )
            
            while True:
                run_status = client.beta.threads.runs.retrieve(
                    thread_id=thread_id,
                    run_id=run.id
                )
                if run_status.status == 'completed':
                    break
                time.sleep(1)
            
            messages = client.beta.threads.messages.list(thread_id=thread_id)
            response_text = messages.data[0].content[0].text.value
            
            # 마크다운 코드 블록 제거
            if response_text.startswith('```'):
                response_text = response_text.split('```')[1]
                if response_text.startswith('json'):
                    response_text = response_text[4:].strip()
            
            print(f"Cleaned response text: {response_text}")
            
            try:
                response = json.loads(response_text)
                if response.get('type') == 'QUIZ' and 'questions' in response:
                    questions = response['questions']
                    if len(questions) != question_count:
                        print(f"Expected {question_count} questions but got {len(questions)}")
                        return self.get_quiz(thread_id, question_count)
                    
                    return {
                        'type': 'QUIZ',
                        'questions': questions,
                        'current_question': 0,
                        'total_questions': len(questions)
                    }
                
                # 형식이 맞지 않는 경우 재시도
                return self.get_quiz(thread_id, question_count)
                
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}")
                print(f"Response text: {response_text}")
                # JSON 파싱 실패 시 재시도
                return self.get_quiz(thread_id, question_count)
            
        except Exception as e:
            print(f"Error getting quiz: {str(e)}")
            return {
                'type': 'ERROR',
                'message': '퀴즈를 가져오는 중 오류가 발생했습니다.'
            }

    def check_answer(self, message, thread_id):
        try:
            client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=message
            )
            
            run = client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=self.assistant_id
            )
            
            while True:
                run_status = client.beta.threads.runs.retrieve(
                    thread_id=thread_id,
                    run_id=run.id
                )
                if run_status.status == 'completed':
                    break
                time.sleep(1)
            
            messages = client.beta.threads.messages.list(thread_id=thread_id)
            response_text = messages.data[0].content[0].text.value
            
            if response_text.startswith('```'):
                response_text = response_text.split('```')[1]
                if response_text.startswith('json'):
                    response_text = response_text[4:].strip()
            
            print(f"Cleaned response text: {response_text}")
            
            try:
                response = json.loads(response_text)
                
                # 응답 타입에 따른 처리
                if response.get('type') == 'ANSWER':
                    # 답변 저장 로직은 chat 라우트에서 처리하므로 여기서는 제거
                    return response
                
                # QUIZ 타입 응답 처리
                elif response.get('type') == 'QUIZ':
                    current_quiz_store[thread_id] = response.get('quiz')
                    return response
                
                # CHAT 타입을 ANSWER 형식으로 변환
                elif response.get('type') == 'CHAT':
                    return {
                        'type': 'ANSWER',
                        'answer': {
                            'correct': None,
                            'explanation': response.get('message', '')
                        }
                    }
                else:
                    raise ValueError("Invalid response format")
                
            except json.JSONDecodeError as e:
                return {
                    'type': 'ANSWER',
                    'answer': {
                        'correct': None,
                        'explanation': response_text
                    }
                }
            
        except Exception as e:
            print(f"Error checking answer: {str(e)}")
            return {
                'type': 'ERROR',
                'message': '답변 체크 중 오류가 발생했습니다.'
            }

    def get_chat_response(self, message, thread_id):
        try:
            client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=message
            )
            
            run = client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=self.assistant_id
            )
            
            while True:
                run_status = client.beta.threads.runs.retrieve(
                    thread_id=thread_id,
                    run_id=run.id
                )
                if run_status.status == 'completed':
                    break
                time.sleep(1)
            
            messages = client.beta.threads.messages.list(thread_id=thread_id)
            response = messages.data[0].content[0].text.value
            
            return json.loads(response)
            
        except Exception as e:
            print(f"Error getting chat response: {str(e)}")
            return {
                'type': 'ERROR',
                'message': '응답 처리 중 오류가 발생했습니다.'
            }

# ScienceQuizBot 인스턴스 생성
quiz_bot = ScienceQuizBot()

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('quiz_page'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            if user.username == 'admin':  # 관리자는 일반 로그인 불가
                flash('관리자는 관리자 로그인 페이지를 이용해주세요.', 'error')
                return redirect(url_for('admin_login'))
            
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('quiz_page'))
        else:
            flash('아이디 또는 비밀번호가 올바르지 않습니다.', 'error')
    
    return render_template('login.html')

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if current_user.is_authenticated:
        if current_user.username == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('quiz_page'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username != 'admin':
            flash('관리자 계정이 아닙니다.', 'error')
            return redirect(url_for('admin_login'))
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            login_user(user)
            next_page = request.args.get('next')
            return redirect(next_page or url_for('admin_dashboard'))
        else:
            flash('아이디 또는 비밀번호가 올바르지 않습니다.', 'error')
    
    return render_template('admin_login.html')

@app.route('/')
@login_required
def quiz_page():
    return render_template('quiz.html')

@app.route('/api/quiz/new', methods=['POST'])
@login_required
def new_quiz():
    try:
        # 새로운 thread 생성
        thread = client.beta.threads.create()
        thread_id = thread.id

        # 요청된 문제 수 확인 (기본값: 1)
        data = request.get_json() or {}
        message = data.get('message', '테스트 시작').strip()
        question_count = 1

        if '문제 출제' in message:
            if '1문제' in message:
                question_count = 1
            elif '5문제' in message:
                question_count = 5
            elif '10문제' in message:
                question_count = 10

        print(f"=== {question_count}문제 출제 시작 ===")
        response = quiz_bot.get_quiz(thread_id, question_count)
        
        if response.get('type') == 'QUIZ':
            print(json.dumps(response, indent=4, ensure_ascii=False))
            
            if question_count > 1 and 'questions' in response:
                # 첫 번째 문제 반환
                first_question = response['questions'][0]
                current_quiz_store[thread_id] = {
                    'questions': response['questions'],
                    'current_index': 0,
                    'total_questions': question_count
                }
                return jsonify({
                    'type': 'QUIZ',
                    'quiz': first_question,
                    'progress': {
                        'current': 1,
                        'total': question_count
                    },
                    'thread_id': thread_id
                })
            
            # 단일 문제인 경우
            current_quiz_store[thread_id] = response.get('questions')[0]
            response['thread_id'] = thread_id
            return jsonify(response)
        else:
            raise ValueError("Invalid quiz format")
            
    except Exception as e:
        print(f"Error in new_quiz: {str(e)}")
        return jsonify({
            'type': 'ERROR',
            'message': '퀴즈를 생성하는 중 오류가 발생했습니다.'
        }), 500

@app.route('/api/quiz/answer', methods=['POST'])
def submit_answer():
    data = request.json
    thread_id = data.get('thread_id')
    answer = data.get('answer')
    
    quiz_bot = ScienceQuizBot()
    result = quiz_bot.check_answer(answer, thread_id)
    
    return jsonify(result)

@app.route('/api/chat', methods=['POST'])
@login_required
def chat():
    try:
        data = request.get_json()
        message = data.get('message', '').strip()
        thread_id = data.get('thread_id')
        is_quiz_answer = data.get('is_quiz_answer', False)  # 퀴즈 답변 여부 확인
        
        print("=== 받은 메시지 ===")
        print(message)
        print(f"Thread ID: {thread_id}")
        print(f"Is Quiz Answer: {is_quiz_answer}")
        
        if not thread_id:
            return jsonify({
                'type': 'ERROR',
                'message': '유효하지 않은 세션입니다.'
            }), 400
        
        # 문제 수 확인
        question_count = None
        if '문제 출제' in message:
            if '1문제' in message:
                question_count = 1
            elif '5문제' in message:
                question_count = 5
            elif '10문제' in message:
                question_count = 10
            print(f"=== 요청된 문제 수: {question_count} ===")
        
        # 현재 진행 중인 퀴즈가 있는지 확인
        current_quiz = current_quiz_store.get(thread_id)
        
        if question_count is not None:
            print(f"=== {question_count}문제 출제 시작 ===")
            response = quiz_bot.get_quiz(thread_id, question_count)
            
            if question_count > 1 and 'questions' in response:
                # 첫 번째 문제 반환
                first_question = response['questions'][0]
                current_quiz_store[thread_id] = {
                    'questions': response['questions'],
                    'current_index': 0,
                    'total_questions': question_count
                }
                return jsonify({
                    'type': 'QUIZ',
                    'quiz': first_question,
                    'progress': {
                        'current': 1,
                        'total': question_count
                    }
                })
            else:
                # 1문제 출제의 경우
                if 'questions' in response and len(response['questions']) > 0:
                    current_quiz_store[thread_id] = response['questions'][0]
                    return jsonify({
                        'type': 'QUIZ',
                        'quiz': response['questions'][0]
                    })
            return jsonify(response)
            
        elif current_quiz and isinstance(current_quiz, dict):
            print("=== 답변 체크 시작 ===")
            response = quiz_bot.check_answer(message, thread_id)
            
            if response.get('type') == 'ANSWER':
                # 여러 문제인 경우
                if 'questions' in current_quiz:
                    current_index = current_quiz['current_index']
                    total_questions = current_quiz['total_questions']
                    current_question = current_quiz['questions'][current_index]
                    
                    # 답변 저장 - 중복 저장 방지
                    if current_user.is_authenticated:
                        answer = Answer(
                            user_id=current_user.id,
                            unit=current_question['unit'],
                            question=current_question['question'],
                            user_answer=message,
                            is_correct=response['answer'].get('correct', False),
                            timestamp=datetime.utcnow()
                        )
                        db.session.add(answer)
                        db.session.commit()
                        
                        print("=== 답변 저장 완료 ===")
                        print(f"단원: {current_question['unit']}")
                        print(f"정답여부: {response['answer'].get('correct')}")
                    
                    if current_index + 1 < total_questions:
                        # 다음 문제 준비
                        current_quiz['current_index'] += 1
                        next_question = current_quiz['questions'][current_quiz['current_index']]
                        response['next_question'] = {
                            'type': 'QUIZ',
                            'quiz': next_question,
                            'progress': {
                                'current': current_quiz['current_index'] + 1,
                                'total': total_questions
                            }
                        }
                    else:
                        response['quiz_completed'] = True
                # 단일 문제인 경우
                else:
                    if current_user.is_authenticated:
                        answer = Answer(
                            user_id=current_user.id,
                            unit=current_quiz['unit'],
                            question=current_quiz['question'],
                            user_answer=message,
                            is_correct=response['answer'].get('correct', False),
                            timestamp=datetime.utcnow()
                        )
                        db.session.add(answer)
                        db.session.commit()
                        
                        print("=== 답변 저장 완료 ===")
                        print(f"단원: {current_quiz['unit']}")
                        print(f"정답여부: {response['answer'].get('correct')}")
            
            return jsonify(response)
            
        else:
            print("=== 일반 질문 처리 ===")
            response = quiz_bot.get_chat_response(message, thread_id)
            
            if response.get('type') == 'CHAT':
                explanation = response.get('message', '')
                response = {
                    'type': 'ANSWER',
                    'answer': {
                        'correct': None,
                        'explanation': explanation
                    }
                }
            
            return jsonify(response)
        
    except Exception as e:
        print(f"Error in chat: {str(e)}")
        return jsonify({
            'type': 'ERROR',
            'message': '죄송합니다. 오류가 발생했습니다.'
        })

@app.route('/admin')
@login_required
def admin_dashboard():
    if current_user.username != 'admin':
        flash('관리자 권한이 필요합니다.', 'error')
        return redirect(url_for('admin_login'))
        
    try:
        # 전체 학생 목록 조회 (admin 제외)
        students = User.query.filter(User.username != 'admin').all()
        
        # 단원별 통계를 위한 선택된 학생 ID
        selected_student_id = request.args.get('student_id', type=int)
        
        # 전체 학생 수
        total_students = len(students)
        
        # 총 문제 풀이 수와 정답 수 (전체)
        total_answers = Answer.query.count()
        total_correct = Answer.query.filter_by(is_correct=True).count()
        
        # 전체 정답률
        accuracy_rate = (total_correct / total_answers * 100) if total_answers > 0 else 0
        
        # 평균 학습 진도율 계산 (100문제 기준)
        student_progress = db.session.query(
            User.id,
            func.count(Answer.id).label('total_answers')
        ).join(Answer, User.id == Answer.user_id)\
         .filter(User.username != 'admin')\
         .group_by(User.id).all()
        
        progress_count = len(student_progress)
        if progress_count > 0:
            total_progress = sum(min(total/100 * 100, 100) for _, total in student_progress)
            average_progress = total_progress / progress_count
        else:
            average_progress = 0
        
        # 단원별 통계 쿼리
        unit_stats_query = db.session.query(
            Answer.unit,
            func.count(Answer.id).label('attempts'),
            func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct'),
            func.count(distinct(Answer.user_id)).label('unique_students')
        )
        
        # 선택된 학생이 있는 경우 해당 학생의 통계만 조회
        if selected_student_id:
            unit_stats_query = unit_stats_query.filter(Answer.user_id == selected_student_id)
        
        unit_stats = unit_stats_query.group_by(Answer.unit).all()
        
        unit_stats = [{
            'name': stat.unit,
            'total_questions': 100,
            'attempts': stat.attempts,
            'correct': stat.correct,
            'accuracy_rate': (stat.correct / stat.attempts * 100) if stat.attempts > 0 else 0,
            'unique_students': stat.unique_students if not selected_student_id else 1
        } for stat in unit_stats]
        
        # 학생별 통계 (단원별 통계와 독립적)
        student_stats = db.session.query(
            User,
            func.count(Answer.id).label('total'),
            func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct')
        ).join(Answer, User.id == Answer.user_id)\
         .filter(User.username != 'admin')\
         .group_by(User.id).all()
        
        return render_template('admin.html',
                             students=students,
                             selected_student_id=selected_student_id,
                             total_students=total_students,
                             total_answers=total_answers,
                             accuracy_rate=accuracy_rate,
                             average_progress=average_progress,
                             unit_stats=unit_stats,
                             student_stats=student_stats)
                             
    except Exception as e:
        print(f"Error in admin_dashboard: {str(e)}")
        flash('대시보드 로딩 중 오류가 발생했습니다.', 'error')
        return redirect(url_for('admin_login'))

@app.route('/logout')
@login_required
def logout():
    # 현재 사용자가 관리자인지 확인
    is_admin = current_user.username == 'admin'
    logout_user()
    
    if is_admin:
        flash('관리자 계정에서 로그아웃되었습니다.')
        return redirect(url_for('admin_login'))
    else:
        flash('로그아웃되었습니다.')
        return redirect(url_for('login'))

@app.route('/admin/users')
@login_required
def user_management():
    if current_user.username != 'admin':
        flash('관리자 권한이 필요합니다.', 'error')
        return redirect(url_for('admin_login'))
        
    users = User.query.all()
    return render_template('user_management.html', users=users)

@app.route('/admin/users/add', methods=['POST'])
@login_required
def add_user():
    if current_user.username != 'admin':
        flash('관리자 권한이 필요합니다.', 'error')
        return redirect(url_for('admin_login'))
        
    username = request.form.get('username')
    password = request.form.get('password')
    
    if not username or not password:
        flash('사용자명과 비밀번호를 모두 입력해주세요.', 'error')
        return redirect(url_for('user_management'))
        
    if User.query.filter_by(username=username).first():
        flash('이미 존재하는 사용자명입니다.', 'error')
        return redirect(url_for('user_management'))
        
    try:
        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        flash('계정이 생성되었습니다.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('계정 생성 중 오류가 발생했습니다.', 'error')
        print(f"Error creating user: {str(e)}")
        
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
@login_required
def delete_user(user_id):
    if current_user.username != 'admin':
        flash('관리자 권한이 필요합니다.', 'error')
        return redirect(url_for('admin_login'))
    
    try:
        user = User.query.get_or_404(user_id)
        if user.username == 'admin':
            flash('관리자 계정은 삭제할 수 없습니다.', 'error')
            return redirect(url_for('user_management'))
            
        # 사용자의 답변 기록도 함께 삭제
        Answer.query.filter_by(user_id=user_id).delete()
        db.session.delete(user)
        db.session.commit()
        
        flash('계정이 삭제되었습니다.', 'success')
    except Exception as e:
        db.session.rollback()
        flash('계정 삭제 중 오류가 발생했습니다.', 'error')
        print(f"Error deleting user: {str(e)}")
    
    return redirect(url_for('user_management'))

@app.route('/admin/stats/delete/<int:user_id>', methods=['POST'])
@login_required
def delete_user_stats(user_id):
    if current_user.username != 'admin':
        return jsonify({'error': '권한이 없습니다.'}), 403
        
    try:
        # 해당 사용자의 모든 답변 기록 삭제
        Answer.query.filter_by(user_id=user_id).delete()
        db.session.commit()
        flash('통계가 삭제되었습니다.', 'success')
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting user stats: {str(e)}")
        return jsonify({'error': '통계 삭제 중 오류가 발생했습니다.'}), 500

@app.route('/admin/stats/delete-all', methods=['POST'])
@login_required
def delete_all_stats():
    if current_user.username != 'admin':
        return jsonify({'error': '권한이 없습니다.'}), 403
        
    try:
        # 모든 답변 기록 삭제
        Answer.query.delete()
        db.session.commit()
        flash('모든 통계가 삭제되었습니다.', 'success')
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        print(f"Error deleting all stats: {str(e)}")
        return jsonify({'error': '통계 삭제 중 오류가 발생했습니다.'}), 500

@app.route('/admin/stats/standardize-units', methods=['POST'])
@login_required
def standardize_unit_names():
    if current_user.username != 'admin':
        return jsonify({'error': '권한이 없습니다.'}), 403
        
    try:
        # '화학 반응의 규칙과 에너지 변화' 단원명 표준화
        answers = Answer.query.filter(
            Answer.unit.like('%화학%반응%규칙%에너지%변화%')
        ).all()
        
        standardized_count = 0
        for answer in answers:
            if answer.unit != '화학 반응의 규칙과 에너지 변화':
                answer.unit = '화학 반응의 규칙과 에너지 변화'
                standardized_count += 1
        
        db.session.commit()
        flash(f'단원명 표준화가 완료되었습니다. {standardized_count}개의 레코드가 수정되었습니다.', 'success')
        return jsonify({
            'success': True,
            'standardized_count': standardized_count
        })
    except Exception as e:
        db.session.rollback()
        print(f"Error standardizing unit names: {str(e)}")
        return jsonify({'error': '단원명 표준화 중 오류가 발생했습니다.'}), 500

@app.route('/admin/stats/unit-report')
@login_required
def download_unit_stats():
    if current_user.username != 'admin':
        flash('관리자 권한이 필요합니다.', 'error')
        return redirect(url_for('admin_login'))
        
    student_id = request.args.get('student_id')
    
    # 단원별 통계 쿼리
    query = db.session.query(
        Answer.unit,
        func.count(Answer.id).label('attempts'),
        func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct'),
        func.count(distinct(Answer.user_id)).label('unique_students')
    )
    
    if student_id:
        query = query.filter(Answer.user_id == student_id)
    
    unit_stats = query.group_by(Answer.unit).all()
    
    # 선택된 학생 정보
    selected_student = User.query.get(student_id) if student_id else None
    
    html = render_template('stats_report.html',
                         generated_at=datetime.utcnow(),
                         report_type='unit',
                         selected_student=selected_student,
                         unit_stats=[{
                             'name': stat.unit,
                             'attempts': stat.attempts,
                             'correct': stat.correct,
                             'accuracy_rate': (stat.correct / stat.attempts * 100) if stat.attempts > 0 else 0,
                             'unique_students': stat.unique_students
                         } for stat in unit_stats])
    
    response = make_response(html)
    response.headers['Content-Type'] = 'text/html'
    response.headers['Content-Disposition'] = f'attachment; filename=unit_stats_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.html'
    return response

@app.route('/admin/stats/student-report')
@login_required
def download_student_stats():
    if current_user.username != 'admin':
        flash('관리자 권한이 필요합니다.', 'error')
        return redirect(url_for('admin_login'))
        
    # 학생별 통계 쿼리
    student_stats = db.session.query(
        User,
        func.count(Answer.id).label('total'),
        func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct')
    ).join(Answer).filter(User.username != 'admin').group_by(User.id).all()
    
    html = render_template('stats_report.html',
                         generated_at=datetime.utcnow(),
                         report_type='student',
                         student_stats=student_stats)
    
    response = make_response(html)
    response.headers['Content-Type'] = 'text/html'
    response.headers['Content-Disposition'] = f'attachment; filename=student_stats_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.html'
    return response

@app.route('/api/admin/statistics/download')
@login_required
def download_statistics():
    if current_user.username != 'admin':
        flash('관리자 권한이 필요합니다.', 'error')
        return redirect(url_for('admin_login'))
        
    # 전체 통계 (단원별 + 학생별)
    unit_stats = db.session.query(
        Answer.unit,
        func.count(Answer.id).label('attempts'),
        func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct'),
        func.count(distinct(Answer.user_id)).label('unique_students')
    ).group_by(Answer.unit).all()
    
    student_stats = db.session.query(
        User,
        func.count(Answer.id).label('total'),
        func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct')
    ).join(Answer).filter(User.username != 'admin').group_by(User.id).all()
    
    html = render_template('stats_report.html',
                         generated_at=datetime.utcnow(),
                         report_type='both',
                         unit_stats=[{
                             'name': stat.unit,
                             'attempts': stat.attempts,
                             'correct': stat.correct,
                             'accuracy_rate': (stat.correct / stat.attempts * 100) if stat.attempts > 0 else 0,
                             'unique_students': stat.unique_students
                         } for stat in unit_stats],
                         student_stats=student_stats)
    
    response = make_response(html)
    response.headers['Content-Type'] = 'text/html'
    response.headers['Content-Disposition'] = f'attachment; filename=statistics_report_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.html'
    return response

if __name__ == '__main__':
    app.run(debug=True)