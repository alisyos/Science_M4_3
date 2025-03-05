from flask import Flask, request, jsonify, render_template, session, redirect, url_for, flash, make_response
import openai
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

# OpenAI API 키 설정
openai.api_key = api_key

# 전역 변수로 current_quiz 저장
current_quiz_store = {}

# ScienceQuizBot 클래스 정의
class ScienceQuizBot:
    def __init__(self):
        self.assistant_id = os.getenv('ASSISTANT_ID')
        self.current_quiz = None
        if not self.assistant_id:
            raise ValueError("Assistant ID not found in environment variables")

    def get_quiz(self, thread_id):
        try:
            # 명확한 퀴즈 생성 요청
            openai.api_key = api_key
            openai.api_base = "https://api.openai.com/v1"  # 기본 OpenAI API 엔드포인트 사용
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": "새로운 과학 퀴즈를 생성해주세요. 반드시 QUIZ 타입으로 응답해주세요."}],
                max_tokens=1000,
                n=1,
                stop=None,
                temperature=0.8,
            )
            
            response_text = response.choices[0].message['content']
            
            # 마크다운 코드 블록 제거
            if response_text.startswith('```'):
                response_text = response_text.split('```')[1]
                if response_text.startswith('json'):
                    response_text = response_text[4:].strip()
            
            print(f"Cleaned response text: {response_text}")
            
            try:
                response = json.loads(response_text)
                if response.get('type') == 'QUIZ' and 'quiz' in response:
                    self.current_quiz = response.get('quiz')
                    return response
                else:
                    # 퀴즈 형식이 아닌 경우 재시도
                    return self.get_quiz(thread_id)
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}")
                print(f"Response text: {response_text}")
                # JSON 파싱 실패 시 재시도
                return self.get_quiz(thread_id)
            
        except Exception as e:
            print(f"Error getting quiz: {str(e)}")
            return {
                'type': 'ERROR',
                'message': '퀴즈를 가져오는 중 오류가 발생했습니다.'
            }

    def check_answer(self, message, thread_id):
        try:
            openai.api_key = api_key
            openai.api_base = "https://api.openai.com/v1"  # 기본 OpenAI API 엔드포인트 사용
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": message}],
                max_tokens=1000,
                n=1,
                stop=None,
                temperature=0.8,
            )
            
            response_text = response.choices[0].message['content']
            
            if response_text.startswith('```'):
                response_text = response_text.split('```')[1]
                if response_text.startswith('json'):
                    response_text = response_text[4:].strip()
            
            print(f"Cleaned response text: {response_text}")
            
            try:
                response = json.loads(response_text)
                
                # ANSWER 타입 응답 처리 및 DB 저장
                if response.get('type') == 'ANSWER':
                    # 현재 퀴즈 정보 가져오기
                    current_quiz = current_quiz_store.get(thread_id)
                    if current_quiz and current_user.is_authenticated:
                        # DB에 답변 저장
                        answer = Answer(
                            user_id=current_user.id,
                            unit=current_quiz.get('unit', ''),
                            question=current_quiz.get('question', ''),
                            user_answer=message,
                            is_correct=response['answer'].get('correct', False),
                            timestamp=datetime.utcnow()
                        )
                        db.session.add(answer)
                        db.session.commit()
                        
                        print("=== 답변 저장 완료 ===")
                        print(f"단원: {current_quiz.get('unit')}")
                        print(f"정답여부: {response['answer'].get('correct')}")
                    
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
            openai.api_key = api_key
            openai.api_base = "https://api.openai.com/v1"  # 기본 OpenAI API 엔드포인트 사용
            response = openai.ChatCompletion.create(
                model="gpt-3.5-turbo",
                messages=[{"role": "user", "content": message}],
                max_tokens=1000,
                n=1,
                stop=None,
                temperature=0.8,
            )
            
            response_text = response.choices[0].message['content']
            
            return json.loads(response_text)
            
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
        response = quiz_bot.get_quiz(thread_id)
        
        if response.get('type') == 'QUIZ':
            print(json.dumps(response, indent=4, ensure_ascii=False))
            quiz = response.get('quiz')
            
            # 현재 퀴즈 정보 저장
            current_quiz_store[thread_id] = quiz
            
            print("=== 퀴즈 정보 저장 ===")
            print(f"스레드: {thread_id}")
            print(f"퀴즈: {quiz}")
            
            # thread_id를 응답에 포함
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
        
        print("=== 받은 메시지 ===")
        print(message)
        print(f"Thread ID: {thread_id}")  # thread_id 로깅 추가
        
        if not thread_id:
            return jsonify({
                'type': 'ERROR',
                'message': '유효하지 않은 세션입니다.'
            }), 400
        
        # 현재 진행 중인 퀴즈가 있는지 확인
        current_quiz = current_quiz_store.get(thread_id)
        
        if current_quiz:
            print("=== 답변 체크 시작 ===")
            response = quiz_bot.check_answer(message, thread_id)
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
        
        print("=== 답변 결과 ===")
        print(response)
        
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