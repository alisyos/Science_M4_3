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
import traceback
import logging

# 로깅 설정
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("quiz_app.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

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

# 전역 변수로 current_quiz_store 저장
current_quiz_store = {}

# 쓰레드 ID별 활성 요청 상태 추적
active_requests = {}

# 임시 파일 저장 디렉토리 확인 및 생성
temp_dir = os.path.join(os.path.dirname(__file__), 'temp')
if not os.path.exists(temp_dir):
    os.makedirs(temp_dir)

# 타임아웃 클래스 추가
class TimeoutError(Exception):
    """요청 시간이 초과되었을 때 발생하는 예외"""
    pass

# ScienceQuizBot 클래스 정의
class ScienceQuizBot:
    def __init__(self):
        # 시스템 프롬프트를 제거하고 Assistant API의 설정을 활용합니다
        # self.system_prompt는 사용하지 않도록 수정
        self.assistant_id = os.environ.get("ASSISTANT_ID")
        if not self.assistant_id:
            raise ValueError("ASSISTANT_ID 환경변수가 설정되지 않았습니다.")
        
        # 다른 초기화 코드는 유지...

    def get_quiz(self, thread_id, question_count=1, main_unit=None, sub_unit=None, question_types=None):
        logger.info(f"문제 출제 요청: thread_id={thread_id}, 문제 수={question_count}, 과목={main_unit}, 학년={sub_unit}, 문제 유형={question_types}")
        try:
            # 기본값 설정
            if question_types is None or len(question_types) == 0:
                question_types = ['객관식']
            
            print("=== 전송하는 프롬프트 ===")
            
            # 스레드 ID가 없는 경우 새로 생성
            if not thread_id:
                print("get_quiz에서 새 스레드 ID 생성")
                thread = client.beta.threads.create()
                thread_id = thread.id
                print(f"생성된 Thread ID: {thread_id}")
            
            # 단위 파라미터 준비
            subject = main_unit
            grade = sub_unit
            
            # 프롬프트 준비
            prompt_parts = []
            if subject:
                prompt_parts.append(f"과목: {subject}")
            if grade:
                prompt_parts.append(f"학년: {grade}")
            
            # 문제 유형 텍스트 구성
            type_text = "문제 유형: "
            if len(question_types) == 1:
                type_text += question_types[0]
            else:
                type_text += ", ".join(question_types[:-1]) + " 및 " + question_types[-1]
            prompt_parts.append(type_text)
            
            # 문제 수 지정
            prompt_parts.append(f"{question_count}개의 문제를 출제해주세요.")
            prompt = "\n".join(prompt_parts)
            
            print(prompt)
            print("========================")
            
            # 사용자 메시지 추가
            try:
                client.beta.threads.messages.create(
                    thread_id=thread_id,
                    role="user",
                    content=prompt
                )
            except Exception as e:
                print(f"메시지 생성 에러: {str(e)}")
                return {"type": "ERROR", "message": f"메시지 생성 에러: {str(e)}"}
            
            # 응답 생성 요청 
            try:
                run = client.beta.threads.runs.create(
                    thread_id=thread_id,
                    assistant_id=self.assistant_id
                )
                
                # 완료 대기
                while True:
                    run_status = client.beta.threads.runs.retrieve(
                        thread_id=thread_id,
                        run_id=run.id
                    )
                    if run_status.status == 'completed':
                        break
                    elif run_status.status in ['failed', 'cancelled', 'expired']:
                        return {"type": "ERROR", "message": f"응답 생성 실패: {run_status.status}"}
                    time.sleep(1)
                
                # 응답 메시지 가져오기
                messages = client.beta.threads.messages.list(
                    thread_id=thread_id
                )
                
                # 첫 번째 메시지(최신) 가져오기
                response_message = messages.data[0].content[0].text.value
                
                # JSON 응답 파싱
                try:
                    json_start = response_message.find('{')
                    json_end = response_message.rfind('}') + 1
                    
                    if json_start != -1 and json_end != -1:
                        json_text = response_message[json_start:json_end]
                        try:
                            quiz_data = json.loads(json_text)
                            print("JSON 파싱 성공")
                            
                            # 스레드 ID 추가
                            quiz_data['thread_id'] = thread_id
                            
                            # JSON 파싱 성공 후 퀴즈 정보 저장
                            if 'questions' in quiz_data and quiz_data['questions']:
                                # 여러 문제가 있는 경우
                                current_quiz_store[thread_id] = {
                                    'questions': quiz_data['questions'],
                                    'current_index': 0,
                                    'quiz': quiz_data['questions'][0],
                                    'progress': {
                                        'current': 1,
                                        'total': len(quiz_data['questions'])
                                    }
                                }
                            elif 'quiz' in quiz_data:
                                # 단일 문제인 경우
                                current_quiz_store[thread_id] = {
                                    'quiz': quiz_data['quiz'],
                                    'progress': {
                                        'current': 1,
                                        'total': 1
                                    }
                                }
                            else:
                                print("퀴즈 데이터에 'questions' 또는 'quiz' 필드가 없습니다.")
                            
                            return quiz_data
                        except json.JSONDecodeError as e:
                            print(f"JSON 파싱 오류: {str(e)}")
                            print(f"JSON 텍스트: {json_text}")
                            return {"type": "ERROR", "message": "퀴즈 데이터 형식이 유효하지 않습니다."}
                    else:
                        print(f"JSON 형식을 찾을 수 없습니다. 응답: {response_message}")
                        # JSON이 아닌 일반 텍스트 응답
                        return {
                            "type": "CHAT",
                            "message": response_message,
                            "thread_id": thread_id
                        }
                except Exception as e:
                    print(f"응답 처리 오류: {str(e)}")
                    return {"type": "ERROR", "message": f"응답 처리 오류: {str(e)}"}
                
            except Exception as e:
                print(f"Error in run creation or retrieval: {str(e)}")
                return {"type": "ERROR", "message": f"응답 생성 오류: {str(e)}"}
        
        except Exception as e:
            print(f"Error in get_quiz: {str(e)}")
            traceback.print_exc()
            return {"type": "ERROR", "message": f"퀴즈 생성 중 오류가 발생했습니다: {str(e)}"}

    def check_answer(self, message, thread_id):
        logger.info(f"답변 평가 요청: thread_id={thread_id}, 답변={message}")
        try:
            # 현재 퀴즈 정보 가져오기
            if thread_id not in current_quiz_store:
                print(f"thread_id {thread_id}에 대한 퀴즈 정보가 없습니다.")
                print(f"현재 저장된 쓰레드 IDs: {list(current_quiz_store.keys())}")
                return {"type": "ERROR", "message": "퀴즈 정보를 찾을 수 없습니다. 새로운 문제를 먼저 요청해주세요."}
            
            current_quiz = current_quiz_store.get(thread_id)
            
            # 퀴즈 정보 추출
            quiz = current_quiz.get('quiz', {})
            question = quiz.get('question', '')
            correct_answer = quiz.get('correct', '')
            question_type = quiz.get('question_type', '객관식')
            
            # 로그 출력
            print(f"현재 쓰레드: {thread_id}")
            print(f"사용자 답변: {message}")
            print(f"정답: {correct_answer}")
            print(f"문제 유형: {question_type}")
            print(f"문제: {question}")
            
            # 답변 평가 요청 프롬프트 구성
            prompt = f"""
            다음은 방금 출제한 {question_type} 문제와 사용자의 답변입니다:

            문제: {question}
            정답: {correct_answer}
            사용자 답변: {message}

            사용자 답변이 정답인지 평가하고, 아래 JSON 형식으로 응답해주세요:
            {{
                "type": "ANSWER",
                "answer": {{
                    "correct": true/false,
                    "explanation": "정답/오답에 대한 설명", 
                    "correct_answer": "정답" // 오답인 경우에만 제공
                }}
            }}
            """
            
            print("=== 평가 프롬프트 ===")
            print(prompt)
            print("=====================")
            
            try:
                # 메시지 추가
                client.beta.threads.messages.create(
                    thread_id=thread_id,
                    role="user",
                    content=prompt
                )
                
                # 실행 요청
                run = client.beta.threads.runs.create(
                    thread_id=thread_id,
                    assistant_id=self.assistant_id
                )
                
                # 완료 대기
                print("GPT 응답 대기 중...")
                timeout_seconds = 30
                start_time = time.time()
                
                while True:
                    # 타임아웃 체크
                    if time.time() - start_time > timeout_seconds:
                        print("GPT 응답 타임아웃")
                        raise TimeoutError("GPT 응답 시간 초과")
                    
                    run_status = client.beta.threads.runs.retrieve(
                        thread_id=thread_id,
                        run_id=run.id
                    )
                    
                    print(f"Run 상태: {run_status.status}")
                    
                    if run_status.status == 'completed':
                        break
                    elif run_status.status in ['failed', 'cancelled', 'expired']:
                        raise Exception(f"응답 생성 실패: {run_status.status}")
                    
                    time.sleep(1)
                
                # 응답 가져오기
                messages = client.beta.threads.messages.list(
                    thread_id=thread_id
                )
                
                # 응답 내용 추출
                response_message = messages.data[0].content[0].text.value
                print("GPT 응답:", response_message)
                
                # JSON 응답 파싱
                try:
                    # JSON 부분 추출
                    json_start = response_message.find('{')
                    json_end = response_message.rfind('}') + 1
                    
                    if json_start != -1 and json_end != -1:
                        json_text = response_message[json_start:json_end]
                        print("추출된 JSON:", json_text)
                        
                        result = json.loads(json_text)
                        
                        # 필수 필드 확인 및 추가
                        if "type" not in result:
                            result["type"] = "ANSWER"
                        
                        # 다음 문제 처리
                        self._add_next_question_if_available(thread_id, current_quiz, result)
                        return result
                    else:
                        print("JSON 형식을 찾을 수 없음, 전체 응답:", response_message)
                        # JSON이 없는 경우 기본 응답 생성
                        raise ValueError("유효한 JSON 응답을 찾을 수 없습니다")
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"JSON 파싱 오류: {str(e)}")
                    # 오류 발생 시 기본 평가 방식 사용
                    result = self._create_default_answer_response(message, quiz)
                    # 다음 문제 처리
                    self._add_next_question_if_available(thread_id, current_quiz, result)
                    return result
                
            except Exception as e:
                print(f"GPT 답변 평가 오류: {str(e)}")
                # 오류 발생 시 기본 평가 방식 사용
                result = self._create_default_answer_response(message, quiz)
                # 다음 문제 처리
                self._add_next_question_if_available(thread_id, current_quiz, result)
                return result
            
        except Exception as e:
            print(f"check_answer 메서드 오류: {str(e)}")
            traceback.print_exc()
            return {"type": "ERROR", "message": f"답변 확인 중 오류가 발생했습니다: {str(e)}"}

    def _create_default_answer_response(self, message, quiz):
        """GPT 평가 실패 시 기본 문자열 비교로 답변 평가"""
        correct_answer = quiz.get('correct', '')
        explanation = quiz.get('explanation', '')
        question_type = quiz.get('question_type', '객관식')
        
        # 객관식
        if question_type == '객관식':
            pattern = r'^[①-⑤]\s*'
            user_answer_stripped = re.sub(pattern, '', message.strip())
            correct_stripped = re.sub(pattern, '', correct_answer.strip())
            
            # 번호로 답변한 경우
            if re.match(r'^[①-⑤]$', message.strip()):
                is_correct = message.strip() == correct_answer[0]
            # 내용으로 답변한 경우
            elif user_answer_stripped and correct_stripped:
                is_correct = user_answer_stripped.lower() == correct_stripped.lower()
            else:
                is_correct = message.strip().lower() == correct_answer.strip().lower()
        else:
            # 단답형/빈칸채우기는 단순 문자열 비교
            is_correct = message.strip().lower() == correct_answer.strip().lower()
        
        result = {
            "type": "ANSWER",
            "answer": {
                "correct": is_correct,
                "explanation": explanation
            }
        }
        
        # 오답인 경우 정답 추가
        if not is_correct:
            result["answer"]["correct_answer"] = correct_answer
        
        return result

    def _add_next_question_if_available(self, thread_id, current_quiz, result):
        """다음 문제가 있다면 결과에 추가"""
        if 'questions' in current_quiz:
            questions = current_quiz['questions']
            current_index = current_quiz.get('current_index', 0)
            
            if current_index + 1 < len(questions):
                next_index = current_index + 1
                next_quiz = questions[next_index]
                
                # 다음 문제 정보 저장
                current_quiz_store[thread_id] = {
                    'questions': questions,
                    'current_index': next_index,
                    'quiz': next_quiz,
                    'progress': {
                        'current': next_index + 1,
                        'total': len(questions)
                    }
                }
                
                # 다음 문제 정보 추가
                result['next_question'] = {
                    'quiz': next_quiz,
                    'progress': {
                        'current': next_index + 1,
                        'total': len(questions)
                    }
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
        
        # 필터링 파라미터 가져오기
        subject = data.get('subject')
        grade = data.get('grade')
        unit = data.get('unit')
        question_types = data.get('question_types', ['객관식'])  # 기본값 설정
        
        print("=== 받은 메시지 ===")
        print(f"메시지: {message}")
        print(f"Thread ID: {thread_id}")
        print(f"Is Quiz Answer: {is_quiz_answer}")
        print(f"과목 필터: {subject}")
        print(f"학년 필터: {grade}")
        print(f"단원 필터: {unit}")
        print(f"문제 유형 필터: {question_types}")
        
        # 스레드 ID가 없는 경우 생성
        if not thread_id:
            print("새 스레드 ID 생성")
            thread = client.beta.threads.create()
            thread_id = thread.id
            print(f"생성된 Thread ID: {thread_id}")
        
        # 퀴즈 요청 패턴 확인
        quiz_request_pattern = r'(\d+)문제\s*(출제|내줘|주세요|풀고싶어요|풀래요|풀어볼래요)'
        match = re.search(quiz_request_pattern, message)
        
        if match:
            question_count = int(match.group(1))
            print(f"=== 요청된 문제 수: {question_count} ===")
            
            # 이미 요청에 필터가 포함되어 있는지 확인하고 포함되어 있다면 사용
            # 필터가 없는 경우에만 기본값 사용
            if not subject and not grade and not unit and not question_types:
                print("필터가 없는 요청입니다. 기본값을 사용합니다.")
            
            # 퀴즈 생성 호출시 문제 유형 전달
            result = quiz_bot.get_quiz(
                thread_id=thread_id,
                question_count=question_count,
                main_unit=subject,
                sub_unit=grade,
                question_types=question_types
            )
            
            # 스레드 ID 확인 및 업데이트
            if result and 'thread_id' in result:
                thread_id = result['thread_id']
            
            # 퀴즈 생성 결과에서 데이터 추출
            if result.get('type') == 'QUIZ':
                # 퀴즈 데이터가 있는 경우, 저장
                if 'questions' in result and result['questions']:
                    current_quiz_store[thread_id] = {
                        'questions': result['questions'],
                        'current_index': 0,
                        'quiz': result['questions'][0],
                        'progress': {
                            'current': 1,
                            'total': len(result['questions'])
                        }
                    }
                    print(f"쓰레드 {thread_id}에 대한 퀴즈 정보 저장 완료 (여러 문제)")
                elif 'quiz' in result:
                    current_quiz_store[thread_id] = {
                        'quiz': result['quiz'],
                        'progress': {
                            'current': 1,
                            'total': 1
                        }
                    }
                    print(f"쓰레드 {thread_id}에 대한 퀴즈 정보 저장 완료 (단일 문제)")
            
            return jsonify(result)
            
        elif is_quiz_answer:
            # 현재 퀴즈 정보 로깅
            print("=== 답변 평가 요청 ===")
            if thread_id in current_quiz_store:
                current_quiz = current_quiz_store[thread_id]
                quiz = current_quiz.get('quiz', {})
                print(f"현재 문제: {quiz.get('question', '정보 없음')}")
                print(f"정답: {quiz.get('correct', '정보 없음')}")
                print(f"문제 유형: {quiz.get('question_type', '정보 없음')}")
            else:
                print(f"thread_id {thread_id}에 대한 퀴즈 정보가 없습니다.")
                print(f"현재 저장된 쓰레드 IDs: {list(current_quiz_store.keys())}")
            
            # 답변 체크
            result = quiz_bot.check_answer(message, thread_id)
            
            print("=== 답변 평가 결과 반환 ===")
            print(result)
            return jsonify(result)
            
        else:
            # 일반 대화
            result = quiz_bot.get_chat_response(message, thread_id)
            return jsonify(result)
            
    except Exception as e:
        print(f"Error in chat API: {str(e)}")
        traceback.print_exc()
        return jsonify({"type": "ERROR", "message": f"오류가 발생했습니다: {str(e)}"})

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