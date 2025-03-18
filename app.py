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
import re

# 환경 변수 로드
load_dotenv()

# Flask 앱 설정
app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True

# Flask 설정
app.config['SECRET_KEY'] = os.environ.get('FLASK_SECRET_KEY', 'dev')

# 데이터베이스 파일 경로 설정
db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'quiz.db')

# PostgreSQL 설정
if os.environ.get('DATABASE_URL'):
    database_url = os.environ.get('DATABASE_URL')
    if database_url.startswith('postgres://'):
        database_url = database_url.replace('postgres://', 'postgresql://', 1)
    app.config['SQLALCHEMY_DATABASE_URI'] = database_url
else:
    # 로컬 개발용 SQLite
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'

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
    # 테이블 생성 (테이블이 없는 경우에만 생성됨)
    db.create_all()
    
    # 관리자 계정이 없는 경우 생성
    if not User.query.filter_by(username='admin').first():
        admin = User(username='admin')
        admin.set_password('admin123')
        admin.is_admin = True  # 관리자 권한 부여
        db.session.add(admin)
        db.session.commit()
        print("관리자 계정이 생성되었습니다.")
    
    print("데이터베이스가 연결되었습니다.")

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

    def get_quiz(self, thread_id, question_count=1, main_unit=None, sub_unit=None):
        try:
            # 단원 필터링 조건 추가
            unit_filter = ""
            if main_unit and sub_unit:
                unit_filter = f"""대단원은 '{main_unit}'이고 소단원은 '{sub_unit}'인 문제만 출제해주세요.
반드시 main_unit 필드에는 '{main_unit}'을, sub_unit 필드에는 '{sub_unit}'을 정확히 입력해야 합니다."""
            elif main_unit:
                unit_filter = f"""대단원이 '{main_unit}'인 문제만 출제해주세요.
반드시 main_unit 필드에는 '{main_unit}'을 정확히 입력해야 합니다."""
            
            # 명확한 퀴즈 생성 요청
            prompt = f"""중학교 과학 관련 문제를 {question_count}개 출제해주세요.
{unit_filter}

반드시 다음 JSON 형식을 정확히 따라야 합니다:

{{
  "type": "QUIZ",
  "questions": [
    {{
      "main_unit": "대단원명",
      "sub_unit": "소단원명",
      "question": "문제 내용",
      "options": ["① 보기1", "② 보기2", "③ 보기3", "④ 보기4", "⑤ 보기5"],
      "correct": "정답",
      "type": "용어 정의",
      "explanation": "해설"
    }}
  ]
}}

중요 규칙:
1. questions 배열에 정확히 {question_count}개의 문제가 있어야 합니다.
2. 각 문제는 반드시 main_unit, sub_unit, question, options, correct, type, explanation 필드를 모두 포함해야 합니다.
3. options 배열은 정확히 5개의 보기를 포함해야 합니다.
4. correct는 반드시 options 배열의 요소 중 하나와 정확히 일치해야 합니다.
5. 단원 정보는 반드시 정확하게 입력해야 합니다."""

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
                    
                    # 단원 필터링 검증
                    if main_unit or sub_unit:
                        valid_questions = []
                        for q in questions:
                            if main_unit and sub_unit:
                                if q.get('main_unit') == main_unit and q.get('sub_unit') == sub_unit:
                                    valid_questions.append(q)
                            elif main_unit:
                                if q.get('main_unit') == main_unit:
                                    valid_questions.append(q)
                        
                        # 유효한 문제가 없거나 요청한 수보다 적으면 다시 요청
                        if not valid_questions or len(valid_questions) < question_count:
                            print(f"단원 필터링 조건에 맞는 문제가 부족합니다. 다시 요청합니다.")
                            return self.get_quiz(thread_id, question_count, main_unit, sub_unit)
                        
                        # 유효한 문제만 사용
                        questions = valid_questions[:question_count]
                    
                    if len(questions) != question_count:
                        print(f"Expected {question_count} questions but got {len(questions)}")
                        return self.get_quiz(thread_id, question_count, main_unit, sub_unit)
                    
                    return {
                        'type': 'QUIZ',
                        'questions': questions,
                        'current_question': 0,
                        'total_questions': len(questions)
                    }
                
                # 형식이 맞지 않는 경우 재시도
                return self.get_quiz(thread_id, question_count, main_unit, sub_unit)
                
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}")
                print(f"Response text: {response_text}")
                # JSON 파싱 실패 시 재시도
                return self.get_quiz(thread_id, question_count, main_unit, sub_unit)
            
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
    # 이미 로그인한 경우 처리
    if current_user.is_authenticated:
        if current_user.username == 'admin':
            return redirect(url_for('admin_dashboard'))
        else:
            # 일반 사용자는 퀴즈 페이지로 리디렉션
            return redirect(url_for('quiz_page'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        if username != 'admin':
            flash('관리자 계정이 아닙니다.', 'error')
            return redirect(url_for('admin_login'))
        
        user = User.query.filter_by(username=username).first()
        
        if user and user.check_password(password):
            # 관리자 로그인 성공
            login_user(user)
            print("관리자 로그인 성공, 대시보드로 리디렉션")
            return redirect(url_for('admin_dashboard'))
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

        # 단원 필터 추가
        main_unit = data.get('main_unit')
        sub_unit = data.get('sub_unit')

        print(f"=== {question_count}문제 출제 시작 ===")
        print(f"대단원 필터: {main_unit}")
        print(f"소단원 필터: {sub_unit}")
        
        response = quiz_bot.get_quiz(thread_id, question_count, main_unit, sub_unit)
        
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
            return jsonify({
                'type': 'QUIZ',
                'quiz': response.get('questions')[0],
                'progress': {
                    'current': 1,
                    'total': 1
                },
                'thread_id': thread_id
            })
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
        data = request.json
        message = data.get('message', '')
        thread_id = data.get('thread_id')
        is_quiz_answer = data.get('is_quiz_answer', False)
        
        # 새로운 필터링 파라미터 가져오기
        subject = data.get('subject')
        grade = data.get('grade')
        unit = data.get('unit')
        
        print("=== 받은 메시지 ===")
        print(message)
        print(f"Thread ID: {thread_id}")
        print(f"Is Quiz Answer: {is_quiz_answer}")
        print(f"과목 필터: {subject}")
        print(f"학년 필터: {grade}")
        print(f"단원 필터: {unit}")
        
        # 기존 호환성을 위한 변수들
        main_unit = data.get('main_unit')
        sub_unit = data.get('sub_unit')
        
        if not thread_id:
            return jsonify({"error": "Thread ID is required"}), 400
        
        # 퀴즈 답변 처리
        if is_quiz_answer:
            # 현재 퀴즈 정보 가져오기
            current_quiz = current_quiz_store.get(thread_id)
            if not current_quiz:
                return jsonify({"error": "Quiz not found"}), 404
            
            # 답변 체크
            result = quiz_bot.check_answer(message, thread_id)
            
            if result.get('type') == 'ANSWER':
                # 사용자 답변 저장
                try:
                    if isinstance(current_quiz, dict) and 'questions' in current_quiz:
                        # 여러 문제 중 현재 문제
                        current_index = current_quiz.get('current_index', 0)
                        question = current_quiz['questions'][current_index]
                        
                        # 다음 문제 인덱스 계산
                        next_index = current_index + 1
                        has_next = next_index < len(current_quiz['questions'])
                        
                        # 현재 문제 정보 저장
                        answer = Answer(
                            user_id=current_user.id,
                            main_unit=question.get('main_unit'),
                            sub_unit=question.get('sub_unit'),
                            unit=question.get('unit') or question.get('main_unit'),
                            subject=question.get('subject'),
                            grade=question.get('grade'),
                            question=question.get('question'),
                            user_answer=message,
                            is_correct=result['answer'].get('correct', False)
                        )
                        db.session.add(answer)
                        db.session.commit()
                        
                        # 다음 문제가 있는 경우
                        if has_next:
                            # 현재 인덱스 업데이트
                            current_quiz['current_index'] = next_index
                            next_question = current_quiz['questions'][next_index]
                            
                            # 다음 문제 정보 추가
                            result['next_question'] = {
                                'quiz': next_question,
                                'progress': {
                                    'current': next_index + 1,
                                    'total': len(current_quiz['questions'])
                                }
                            }
                    else:
                        # 단일 문제인 경우
                        answer = Answer(
                            user_id=current_user.id,
                            main_unit=current_quiz.get('main_unit'),
                            sub_unit=current_quiz.get('sub_unit'),
                            unit=current_quiz.get('unit') or current_quiz.get('main_unit'),
                            subject=current_quiz.get('subject'),
                            grade=current_quiz.get('grade'),
                            question=current_quiz.get('question'),
                            user_answer=message,
                            is_correct=result['answer'].get('correct', False)
                        )
                        db.session.add(answer)
                        db.session.commit()
                except Exception as e:
                    print(f"Error saving answer: {str(e)}")
                    db.session.rollback()
                
                return jsonify(result)
        
        # 퀴즈 요청 패턴 확인
        quiz_request_pattern = r'(\d+)문제\s*(출제|내줘|주세요|풀고싶어요|풀래요|풀어볼래요)'
        match = re.search(quiz_request_pattern, message)
        
        if match:
            question_count = int(match.group(1))
            print(f"=== 요청된 문제 수: {question_count} ===")
            print(f"=== {message} 시작 ===")
            print(f"과목 필터: {subject}")
            print(f"학년 필터: {grade}")
            print(f"단원 필터: {unit}")
            
            # 프롬프트 구성
            prompt = ""
            if subject and grade and unit:
                prompt = f"{subject} {grade} {unit} 단원 관련 문제를 {question_count}개 출제해주세요."
            elif subject and grade:
                prompt = f"{subject} {grade} 학년 관련 문제를 {question_count}개 출제해주세요."
            elif subject:
                prompt = f"{subject} 과목 관련 문제를 {question_count}개 출제해주세요."
            else:
                prompt = f"초등학교와 중학교 교육 관련 문제를 {question_count}개 출제해주세요."
            
            prompt += """

반드시 다음 JSON 형식을 정확히 따라야 합니다:

{
  "type": "QUIZ",
  "questions": [
    {
      "subject": "과목명",
      "grade": "학년",
      "unit": "단원명",
      "question": "문제 내용",
      "options": ["① 보기1", "② 보기2", "③ 보기3", "④ 보기4", "⑤ 보기5"],
      "correct": "정답",
      "type": "용어 정의",
      "explanation": "해설"
    }
  ]
}

중요 규칙:
1. questions 배열에 정확히 """ + str(question_count) + """개의 문제가 있어야 합니다.
2. 각 문제는 반드시 subject, grade, unit, question, options, correct, type, explanation 필드를 모두 포함해야 합니다.
3. options 배열은 정확히 5개의 보기를 포함해야 합니다.
4. correct는 반드시 options 배열의 요소 중 하나와 정확히 일치해야 합니다.
5. 단원 정보는 반드시 정확하게 입력해야 합니다.
"""
            
            print("=== 전송하는 프롬프트 ===")
            print(prompt)
            print("========================")
            
            # OpenAI API 호출
            client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=prompt
            )
            
            run = client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=quiz_bot.assistant_id
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
                        # 오류 응답
                        return jsonify({
                            'type': 'ERROR',
                            'message': '문제 생성 중 오류가 발생했습니다.'
                        }), 500
                    
                    if question_count > 1:
                        # 첫 번째 문제 반환
                        first_question = questions[0]
                        current_quiz_store[thread_id] = {
                            'questions': questions,
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
                    
                    # 단일 문제인 경우
                    current_quiz_store[thread_id] = questions[0]
                    return jsonify({
                        'type': 'QUIZ',
                        'quiz': questions[0],
                        'progress': {
                            'current': 1,
                            'total': 1
                        }
                    })
                else:
                    # 형식이 맞지 않는 경우
                    return jsonify({
                        'type': 'ERROR',
                        'message': '문제 생성 중 오류가 발생했습니다.'
                    }), 500
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}")
                return jsonify({
                    'type': 'ERROR',
                    'message': '문제 생성 중 오류가 발생했습니다.'
                }), 500
        else:
            # 일반 대화 처리
            client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=message
            )
            
            run = client.beta.threads.runs.create(
                thread_id=thread_id,
                assistant_id=quiz_bot.assistant_id
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
            
            try:
                response = json.loads(response_text)
                return jsonify(response)
            except json.JSONDecodeError:
                # JSON이 아닌 경우 일반 텍스트 응답
                return jsonify({
                    'type': 'CHAT',
                    'message': response_text
                })
    except Exception as e:
        print(f"Error in chat: {str(e)}")
        return jsonify({
            'type': 'ERROR',
            'message': '메시지 처리 중 오류가 발생했습니다.'
        }), 500

@app.route('/admin')
@login_required
def admin_dashboard():
    # 관리자가 아닌 경우 접근 제한
    if current_user.username != 'admin':
        flash('관리자 권한이 필요합니다.', 'error')
        return redirect(url_for('login'))
    
    print("관리자 대시보드 접근")
    
    try:
        # 전체 학생 목록 조회 (admin 제외)
        students = User.query.filter(User.username != 'admin').all()
        
        # 필터링을 위한 파라미터 가져오기
        selected_student_id = request.args.get('student_id', type=int)
        selected_subject = request.args.get('subject')
        selected_grade = request.args.get('grade')
        
        # 모든 과목 및 학년 목록 조회
        unique_subjects = db.session.query(Answer.subject).distinct().all()
        unique_subjects = [subj[0] for subj in unique_subjects if subj[0]]
        
        unique_grades = db.session.query(Answer.grade).distinct().all()
        unique_grades = [grade[0] for grade in unique_grades if grade[0]]
        
        # 전체 학생 수
        total_students = len(students)
        
        # 통계 쿼리 기본 필터 설정
        base_query_filter = []
        if selected_student_id:
            base_query_filter.append(Answer.user_id == selected_student_id)
        if selected_subject:
            base_query_filter.append(Answer.subject == selected_subject)
        if selected_grade:
            base_query_filter.append(Answer.grade == selected_grade)
        
        # 안전하게 쿼리 실행
        try:
            # 총 문제 풀이 수와 정답 수 (필터 적용)
            total_answers_query = Answer.query
            for filter_condition in base_query_filter:
                total_answers_query = total_answers_query.filter(filter_condition)
            total_answers = total_answers_query.count()
            
            total_correct_query = Answer.query.filter_by(is_correct=True)
            for filter_condition in base_query_filter:
                total_correct_query = total_correct_query.filter(filter_condition)
            total_correct = total_correct_query.count()
            
            # 전체 정답률
            accuracy_rate = (total_correct / total_answers * 100) if total_answers > 0 else 0
        except Exception as e:
            print(f"통계 쿼리 오류: {e}")
            total_answers = 0
            total_correct = 0
            accuracy_rate = 0
        
        # 평균 학습 진도율 계산 (100문제 기준, 필터 적용)
        try:
            student_progress_query = db.session.query(
                User.id,
                func.count(Answer.id).label('total_answers')
            ).join(Answer, User.id == Answer.user_id)\
             .filter(User.username != 'admin')
            
            # 과목 및 학년 필터 적용
            if selected_subject:
                student_progress_query = student_progress_query.filter(Answer.subject == selected_subject)
            if selected_grade:
                student_progress_query = student_progress_query.filter(Answer.grade == selected_grade)
            
            student_progress = student_progress_query.group_by(User.id).all()
            
            progress_count = len(student_progress)
            if progress_count > 0:
                total_progress = sum(min(total/100 * 100, 100) for _, total in student_progress)
                average_progress = total_progress / progress_count
            else:
                average_progress = 0
        except Exception as e:
            print(f"진도율 계산 오류: {e}")
            average_progress = 0
        
        # 단원별 통계 쿼리
        try:
            unit_stats_query = db.session.query(
                Answer.subject,
                Answer.grade,
                Answer.unit,
                func.count(Answer.id).label('attempts'),
                func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct'),
                func.count(func.distinct(Answer.user_id)).label('unique_students')
            )
            
            # 필터 적용
            for filter_condition in base_query_filter:
                unit_stats_query = unit_stats_query.filter(filter_condition)
            
            # subject, grade, unit으로 그룹화
            unit_stats = unit_stats_query.group_by(Answer.subject, Answer.grade, Answer.unit).all()
            
            # 결과 가공
            unit_stats = [{
                'subject': stat.subject or '미분류',
                'grade': stat.grade or '',
                'unit': stat.unit or '',
                'name': f"{stat.subject or '미분류'} - {stat.grade or ''} - {stat.unit or ''}",
                'total_questions': 100,
                'attempts': stat.attempts,
                'correct': stat.correct,
                'accuracy_rate': (stat.correct / stat.attempts * 100) if stat.attempts > 0 else 0,
                'unique_students': stat.unique_students if not selected_student_id else 1
            } for stat in unit_stats]
        except Exception as e:
            print(f"단원별 통계 쿼리 오류: {e}")
            unit_stats = []
        
        # 학생별 통계 (단원별 통계와 독립적)
        try:
            student_stats_query = db.session.query(
                User,
                func.count(Answer.id).label('total'),
                func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct')
            ).join(Answer, User.id == Answer.user_id)\
             .filter(User.username != 'admin')
            
            # 과목 및 학년 필터 적용
            if selected_subject:
                student_stats_query = student_stats_query.filter(Answer.subject == selected_subject)
            if selected_grade:
                student_stats_query = student_stats_query.filter(Answer.grade == selected_grade)
            
            # 선택된 학생이 있는 경우 해당 학생의 통계만 조회
            if selected_student_id:
                student_stats_query = student_stats_query.filter(User.id == selected_student_id)
            
            student_stats = student_stats_query.group_by(User.id).all()
        except Exception as e:
            print(f"학생별 통계 쿼리 오류: {e}")
            student_stats = []
        
        # 과목별 통계 데이터 조회
        try:
            subject_stats_query = db.session.query(
                Answer.subject,
                func.count(Answer.id).label('total_questions'),
                func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct_answers'),
                func.sum(case((Answer.is_correct == False, 1), else_=0)).label('incorrect_answers'),
                func.round(func.sum(case((Answer.is_correct == True, 1), else_=0)) * 100.0 / func.count(Answer.id), 1).label('accuracy_rate'),
                func.count(func.distinct(Answer.user_id)).label('unique_students')
            )
            
            # 학생 필터와 학년 필터만 적용 (과목 필터는 제외)
            if selected_student_id:
                subject_stats_query = subject_stats_query.filter(Answer.user_id == selected_student_id)
            if selected_grade:
                subject_stats_query = subject_stats_query.filter(Answer.grade == selected_grade)
            
            # 과목 필터가 있는 경우, 해당 과목만 표시
            if selected_subject:
                subject_stats_query = subject_stats_query.filter(Answer.subject == selected_subject)
            
            subject_stats = subject_stats_query.group_by(Answer.subject).all()
            
            # 결과 가공
            subject_stats_data = [{
                'subject': stat.subject or '미분류',
                'total_questions': stat.total_questions,
                'correct_answers': stat.correct_answers,
                'incorrect_answers': stat.incorrect_answers,
                'accuracy_rate': stat.accuracy_rate or 0,
                'unique_students': stat.unique_students
            } for stat in subject_stats]
        except Exception as e:
            print(f"과목별 통계 쿼리 오류: {e}")
            subject_stats_data = []
        
        # 학년별 통계 데이터 조회
        try:
            grade_stats_query = db.session.query(
                Answer.grade,
                func.count(Answer.id).label('total_questions'),
                func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct_answers'),
                func.sum(case((Answer.is_correct == False, 1), else_=0)).label('incorrect_answers'),
                func.round(func.sum(case((Answer.is_correct == True, 1), else_=0)) * 100.0 / func.count(Answer.id), 1).label('accuracy_rate'),
                func.count(func.distinct(Answer.user_id)).label('unique_students')
            )
            
            # 학생 필터와 과목 필터만 적용 (학년 필터는 제외)
            if selected_student_id:
                grade_stats_query = grade_stats_query.filter(Answer.user_id == selected_student_id)
            if selected_subject:
                grade_stats_query = grade_stats_query.filter(Answer.subject == selected_subject)
            
            # 학년 필터가 있는 경우, 해당 학년만 표시
            if selected_grade:
                grade_stats_query = grade_stats_query.filter(Answer.grade == selected_grade)
            
            grade_stats = grade_stats_query.group_by(Answer.grade).all()
            
            # 결과 가공
            grade_stats_data = [{
                'grade': stat.grade or '미분류',
                'total_questions': stat.total_questions,
                'correct_answers': stat.correct_answers,
                'incorrect_answers': stat.incorrect_answers,
                'accuracy_rate': stat.accuracy_rate or 0,
                'unique_students': stat.unique_students
            } for stat in grade_stats]
        except Exception as e:
            print(f"학년별 통계 쿼리 오류: {e}")
            grade_stats_data = []
        
        return render_template('admin.html',
                             students=students,
                             selected_student_id=selected_student_id,
                             selected_subject=selected_subject,
                             selected_grade=selected_grade,
                             unique_subjects=unique_subjects,
                             unique_grades=unique_grades,
                             total_students=total_students,
                             total_answers=total_answers,
                             accuracy_rate=accuracy_rate,
                             average_progress=average_progress,
                             unit_stats=unit_stats,
                             student_stats=student_stats,
                             subject_stats=subject_stats_data,
                             grade_stats=grade_stats_data)
                             
    except Exception as e:
        print(f"Error in admin_dashboard: {str(e)}")
        import traceback
        traceback.print_exc()  # 상세 오류 정보 출력
        flash('대시보드 로딩 중 오류가 발생했습니다.', 'error')
        return redirect(url_for('login'))

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
    if not current_user.is_authenticated or current_user.username != 'admin':
        flash('관리자 권한이 필요합니다.', 'error')
        return redirect(url_for('login'))
        
    users = User.query.all()
    return render_template('user_management.html', users=users)

@app.route('/admin/users/add', methods=['POST'])
@login_required
def add_user():
    if not current_user.is_authenticated or current_user.username != 'admin':
        flash('관리자 권한이 필요합니다.', 'error')
        return redirect(url_for('login'))
        
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
        # 과목, 학년, 단원 표준화 예시 (화학 반응의 규칙과 에너지 변화)
        answers = Answer.query.filter(
            Answer.unit.like('%화학%반응%규칙%에너지%변화%')
        ).all()
        
        standardized_count = 0
        for answer in answers:
            if answer.unit != '화학 반응의 규칙과 에너지 변화':
                answer.unit = '화학 반응의 규칙과 에너지 변화'
                # 과목과 학년도 함께 표준화
                if '과학' in answer.unit:
                    answer.subject = '과학'
                if not answer.grade and '중3' in answer.unit:
                    answer.grade = '중3'
                standardized_count += 1
        
        # 과목 표준화 (빈 값이나 오타 수정)
        subject_mapping = {
            '과': '과학',
            '과학 ': '과학',
            '과학.': '과학',
            '사': '사회',
            '사회 ': '사회',
            '사회.': '사회',
            '한': '한국사',
            '한국': '한국사',
            '한국사 ': '한국사',
            '한국사.': '한국사'
        }
        
        for wrong, correct in subject_mapping.items():
            answers = Answer.query.filter(Answer.subject == wrong).all()
            for answer in answers:
                answer.subject = correct
                standardized_count += 1
        
        # 학년 표준화
        grade_mapping = {
            '중1학년': '중1',
            '중2학년': '중2',
            '중3학년': '중3',
            '중 1': '중1',
            '중 2': '중2',
            '중 3': '중3',
            '1학년': '중1',
            '2학년': '중2',
            '3학년': '중3'
        }
        
        for wrong, correct in grade_mapping.items():
            answers = Answer.query.filter(Answer.grade == wrong).all()
            for answer in answers:
                answer.grade = correct
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
        return redirect(url_for('login'))
        
    student_id = request.args.get('student_id')
    
    try:
        # 단원별 통계 쿼리 - 새로운 분류 체계 (과목>학년>단원)
        query = db.session.query(
            Answer.subject,
            Answer.grade,
            Answer.unit,
            func.count(Answer.id).label('attempts'),
            func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct'),
            func.count(func.distinct(Answer.user_id)).label('unique_students')
        )
        
        if student_id:
            query = query.filter(Answer.user_id == student_id)
        
        unit_stats = query.group_by(Answer.subject, Answer.grade, Answer.unit).all()
        
        # 선택된 학생 정보
        selected_student = User.query.get(student_id) if student_id else None
        
        html = render_template('stats_report.html',
                             generated_at=datetime.utcnow(),
                             report_type='unit',
                             selected_student=selected_student,
                             unit_stats=[{
                                 'subject': stat.subject or '미분류',
                                 'grade': stat.grade or '',
                                 'unit': stat.unit or '',
                                 'name': f"{stat.subject or '미분류'} - {stat.grade or ''} - {stat.unit or ''}",
                                 'attempts': stat.attempts,
                                 'correct': stat.correct,
                                 'accuracy_rate': (stat.correct / stat.attempts * 100) if stat.attempts > 0 else 0,
                                 'unique_students': stat.unique_students
                             } for stat in unit_stats])
    except Exception as e:
        print(f"단원별 통계 다운로드 오류: {e}")
        # 대체 쿼리: 기존 main_unit, sub_unit 필드 사용 (하위 호환성)
        query = db.session.query(
            Answer.main_unit,
            Answer.sub_unit,
            func.count(Answer.id).label('attempts'),
            func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct'),
            func.count(func.distinct(Answer.user_id)).label('unique_students')
        )
        
        if student_id:
            query = query.filter(Answer.user_id == student_id)
        
        unit_stats = query.group_by(Answer.main_unit, Answer.sub_unit).all()
        
        # 선택된 학생 정보
        selected_student = User.query.get(student_id) if student_id else None
        
        html = render_template('stats_report.html',
                             generated_at=datetime.utcnow(),
                             report_type='unit',
                             selected_student=selected_student,
                             unit_stats=[{
                                 'subject': stat.main_unit or '미분류',
                                 'grade': '',
                                 'unit': stat.sub_unit or '',
                                 'name': f"{stat.main_unit or '미분류'} - {stat.sub_unit or ''}",
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
    ).join(Answer, User.id == Answer.user_id)\
     .filter(User.username != 'admin')\
     .group_by(User.id).all()
    
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
        return redirect(url_for('login'))
    
    # 필터링을 위한 파라미터 가져오기
    selected_student_id = request.args.get('student_id', type=int)
    selected_subject = request.args.get('subject')
    selected_grade = request.args.get('grade')
    
    selected_student = User.query.get(selected_student_id) if selected_student_id else None
    
    # 기본 필터 설정
    base_query_filter = []
    if selected_student_id:
        base_query_filter.append(Answer.user_id == selected_student_id)
    if selected_subject:
        base_query_filter.append(Answer.subject == selected_subject)
    if selected_grade:
        base_query_filter.append(Answer.grade == selected_grade)
    
    try:
        # 1. 학생별 통계 (합산 통계)
        student_stats_query = db.session.query(
            User,
            func.count(Answer.id).label('total'),
            func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct')
        ).join(Answer, User.id == Answer.user_id)\
         .filter(User.username != 'admin')
        
        # 과목 및 학년 필터 적용
        if selected_subject:
            student_stats_query = student_stats_query.filter(Answer.subject == selected_subject)
        if selected_grade:
            student_stats_query = student_stats_query.filter(Answer.grade == selected_grade)
        
        # 학생 필터 적용
        if selected_student_id:
            student_stats_query = student_stats_query.filter(User.id == selected_student_id)
        
        student_stats = student_stats_query.group_by(User.id).all()
        
        # 2. 과목별 통계
        subject_stats_query = db.session.query(
            Answer.subject,
            func.count(Answer.id).label('total_questions'),
            func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct_answers'),
            func.sum(case((Answer.is_correct == False, 1), else_=0)).label('incorrect_answers'),
            func.round(func.sum(case((Answer.is_correct == True, 1), else_=0)) * 100.0 / func.count(Answer.id), 1).label('accuracy_rate'),
            func.count(func.distinct(Answer.user_id)).label('unique_students')
        )
        
        # 학생 필터와 학년 필터 적용
        if selected_student_id:
            subject_stats_query = subject_stats_query.filter(Answer.user_id == selected_student_id)
        if selected_grade:
            subject_stats_query = subject_stats_query.filter(Answer.grade == selected_grade)
        
        # 과목 필터 적용
        if selected_subject:
            subject_stats_query = subject_stats_query.filter(Answer.subject == selected_subject)
        
        subject_stats = subject_stats_query.group_by(Answer.subject).all()
        
        # 3. 학년별 통계
        grade_stats_query = db.session.query(
            Answer.grade,
            func.count(Answer.id).label('total_questions'),
            func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct_answers'),
            func.sum(case((Answer.is_correct == False, 1), else_=0)).label('incorrect_answers'),
            func.round(func.sum(case((Answer.is_correct == True, 1), else_=0)) * 100.0 / func.count(Answer.id), 1).label('accuracy_rate'),
            func.count(func.distinct(Answer.user_id)).label('unique_students')
        )
        
        # 학생 필터와 과목 필터 적용
        if selected_student_id:
            grade_stats_query = grade_stats_query.filter(Answer.user_id == selected_student_id)
        if selected_subject:
            grade_stats_query = grade_stats_query.filter(Answer.subject == selected_subject)
        
        # 학년 필터 적용
        if selected_grade:
            grade_stats_query = grade_stats_query.filter(Answer.grade == selected_grade)
        
        grade_stats = grade_stats_query.group_by(Answer.grade).all()
        
        # 필터 정보 문자열 생성
        filter_info = []
        if selected_student:
            filter_info.append(f"학생: {selected_student.username}")
        if selected_subject:
            filter_info.append(f"과목: {selected_subject}")
        if selected_grade:
            filter_info.append(f"학년: {selected_grade}")
        
        filter_text = " / ".join(filter_info) if filter_info else "전체"
        
        # 통합된 리포트 템플릿 렌더링
        html = render_template('stats_report.html',
                             generated_at=datetime.utcnow(),
                             report_type='complete',  # 모든 통계를 포함하는 새로운 타입
                             selected_student=selected_student,
                             selected_subject=selected_subject,
                             selected_grade=selected_grade,
                             filter_text=filter_text,
                             student_stats=student_stats,
                             subject_stats=subject_stats,
                             grade_stats=grade_stats)
        
        response = make_response(html)
        response.headers['Content-Type'] = 'text/html'
        
        # 파일명에 필터 정보 추가
        filename_parts = []
        if selected_student:
            filename_parts.append(selected_student.username)
        if selected_subject:
            filename_parts.append(selected_subject)
        if selected_grade:
            filename_parts.append(selected_grade)
        
        filename_prefix = "_".join(filename_parts) if filename_parts else "all"
        filename = f"statistics_report_{filename_prefix}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.html"
        response.headers['Content-Disposition'] = f'attachment; filename={filename}'
        return response
        
    except Exception as e:
        print(f"통계 다운로드 오류: {e}")
        import traceback
        traceback.print_exc()
        flash('통계 다운로드 중 오류가 발생했습니다.', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/reset-database', methods=['GET', 'POST'])
@login_required
def reset_database():
    if current_user.username != 'admin':
        flash('관리자 권한이 필요합니다.', 'error')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        try:
            # 모든 답변 기록 삭제
            Answer.query.delete()
            
            # 관리자를 제외한 모든 사용자 삭제
            User.query.filter(User.username != 'admin').delete()
            
            db.session.commit()
            
            # 앱 재시작 필요 메시지
            flash('데이터베이스가 초기화되었습니다. 변경사항을 완전히 적용하려면 서버를 재시작하세요.', 'success')
            
            # 데이터베이스 파일 경로
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'quiz.db')
            
            # 서버 재시작 안내 메시지
            return render_template('restart_server.html', db_path=db_path)
        except Exception as e:
            db.session.rollback()
            print(f"Error resetting database: {str(e)}")
            flash('데이터베이스 초기화 중 오류가 발생했습니다.', 'error')
            return redirect(url_for('admin_dashboard'))
    
    return render_template('reset_database.html')

@app.route('/admin/stats/subject-report')
@login_required
def download_subject_stats():
    if current_user.username != 'admin':
        flash('관리자 권한이 필요합니다.', 'error')
        return redirect(url_for('login'))
    
    try:
        # 과목별 통계 데이터 조회
        subject_stats_query = db.session.query(
            Answer.subject,
            func.count(Answer.id).label('total_questions'),
            func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct_answers'),
            func.sum(case((Answer.is_correct == False, 1), else_=0)).label('incorrect_answers'),
            func.round(func.sum(case((Answer.is_correct == True, 1), else_=0)) * 100.0 / func.count(Answer.id), 1).label('accuracy_rate'),
            func.count(func.distinct(Answer.user_id)).label('unique_students')
        )
        
        # 학생 필터와 학년 필터만 적용 (과목 필터는 제외)
        if selected_student_id:
            subject_stats_query = subject_stats_query.filter(Answer.user_id == selected_student_id)
        if selected_grade:
            subject_stats_query = subject_stats_query.filter(Answer.grade == selected_grade)
        
        # 과목 필터가 있는 경우, 해당 과목만 표시
        if selected_subject:
            subject_stats_query = subject_stats_query.filter(Answer.subject == selected_subject)
        
        subject_stats = subject_stats_query.group_by(Answer.subject).all()
        
        # 결과 가공
        subject_stats_data = [{
            'subject': stat.subject or '미분류',
            'total_questions': stat.total_questions,
            'correct_answers': stat.correct_answers,
            'incorrect_answers': stat.incorrect_answers,
            'accuracy_rate': stat.accuracy_rate or 0,
            'unique_students': stat.unique_students
        } for stat in subject_stats]
        
        html = render_template('stats_report.html',
                             generated_at=datetime.utcnow(),
                             report_type='subject',
                             subject_stats=subject_stats_data)
        
        response = make_response(html)
        response.headers['Content-Type'] = 'text/html'
        response.headers['Content-Disposition'] = f'attachment; filename=subject_stats_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.html'
        return response
    except Exception as e:
        print(f"과목별 통계 다운로드 오류: {e}")
        flash('통계 다운로드 중 오류가 발생했습니다.', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/stats/grade-report')
@login_required
def download_grade_stats():
    if current_user.username != 'admin':
        flash('관리자 권한이 필요합니다.', 'error')
        return redirect(url_for('login'))
    
    try:
        # 학년별 통계 데이터 조회
        grade_stats_query = db.session.query(
            Answer.grade,
            func.count(Answer.id).label('total_questions'),
            func.sum(case((Answer.is_correct == True, 1), else_=0)).label('correct_answers'),
            func.sum(case((Answer.is_correct == False, 1), else_=0)).label('incorrect_answers'),
            func.round(func.sum(case((Answer.is_correct == True, 1), else_=0)) * 100.0 / func.count(Answer.id), 1).label('accuracy_rate'),
            func.count(func.distinct(Answer.user_id)).label('unique_students')
        )
        
        # 학생 필터와 과목 필터만 적용 (학년 필터는 제외)
        if selected_student_id:
            grade_stats_query = grade_stats_query.filter(Answer.user_id == selected_student_id)
        if selected_subject:
            grade_stats_query = grade_stats_query.filter(Answer.subject == selected_subject)
        
        # 학년 필터가 있는 경우, 해당 학년만 표시
        if selected_grade:
            grade_stats_query = grade_stats_query.filter(Answer.grade == selected_grade)
        
        grade_stats = grade_stats_query.group_by(Answer.grade).all()
        
        # 결과 가공
        grade_stats_data = [{
            'grade': stat.grade or '미분류',
            'total_questions': stat.total_questions,
            'correct_answers': stat.correct_answers,
            'incorrect_answers': stat.incorrect_answers,
            'accuracy_rate': stat.accuracy_rate or 0,
            'unique_students': stat.unique_students
        } for stat in grade_stats]
        
        html = render_template('stats_report.html',
                             generated_at=datetime.utcnow(),
                             report_type='grade',
                             grade_stats=grade_stats_data)
        
        response = make_response(html)
        response.headers['Content-Type'] = 'text/html'
        response.headers['Content-Disposition'] = f'attachment; filename=grade_stats_{datetime.utcnow().strftime("%Y%m%d_%H%M%S")}.html'
        return response
    except Exception as e:
        print(f"학년별 통계 다운로드 오류: {e}")
        flash('통계 다운로드 중 오류가 발생했습니다.', 'error')
        return redirect(url_for('admin_dashboard'))

@app.route('/admin/update-categories', methods=['GET', 'POST'])
@login_required
def update_categories():
    if current_user.username != 'admin':
        flash('관리자 권한이 필요합니다.', 'error')
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        try:
            # CSV 또는 텍스트 형식으로 제공된 데이터
            category_data = request.form.get('category_data')
            
            # 데이터 파싱 및 처리
            categories = []
            for line in category_data.strip().split('\n'):
                parts = line.strip().split('\t')
                if len(parts) >= 3:
                    subject = parts[0].strip()
                    grade = parts[1].strip()
                    unit = parts[2].strip()
                    
                    categories.append({
                        'subject': subject,
                        'grade': grade,
                        'unit': unit
                    })
            
            # 데이터베이스에 카테고리 저장 로직 (예: JSON 파일로 저장)
            import json
            with open('categories.json', 'w', encoding='utf-8') as f:
                json.dump(categories, f, ensure_ascii=False, indent=4)
            
            flash(f'{len(categories)}개의 카테고리가 성공적으로 업데이트되었습니다.', 'success')
            return redirect(url_for('admin_dashboard'))
            
        except Exception as e:
            flash(f'카테고리 업데이트 중 오류가 발생했습니다: {str(e)}', 'error')
    
    return render_template('update_categories.html')

@app.route('/api/categories')
def get_categories():
    try:
        import json
        # 저장된 카테고리 JSON 파일 읽기
        with open('categories.json', 'r', encoding='utf-8') as f:
            categories = json.load(f)
        return jsonify(categories)
    except FileNotFoundError:
        # 기본 카테고리 반환
        return jsonify([])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(debug=True)