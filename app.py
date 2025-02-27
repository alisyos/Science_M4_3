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
from database import init_db

app = Flask(__name__)
load_dotenv()

# Flask 설정
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'your-secret-key-here')  # 환경 변수에서 가져오거나 기본값 사용

# Flask-Login 설정
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
login_manager.login_message = "이 페이지에 접근하려면 로그인이 필요합니다."

# 데이터베이스 초기화
init_db(app)

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
        message = client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=f"정답은 {user_answer}입니다."
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
            
            # 현재 퀴즈 정보 추가
            if result.get('type') == 'ANSWER':
                result['quiz'] = self.current_quiz
            return result
        else:
            raise ValueError("JSON 형식의 응답을 찾을 수 없습니다.")

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
        
        # 퀴즈 정보를 전역 저장소에 저장
        if quiz_data.get('type') == 'QUIZ':
            thread_id = quiz_data.get('thread_id')
            current_quiz_store[thread_id] = quiz_data.get('quiz')
            print(f"\n=== 퀴즈 정보 저장 ===\n스레드: {thread_id}\n퀴즈: {quiz_data.get('quiz')}")
            
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
        
        print(f"\n=== 받은 메시지 ===\n{message}")
        
        if not thread_id or not message:
            return jsonify({"error": "thread_id와 message는 필수입니다."}), 400
            
        quiz_bot = ScienceQuizBot()
        
        # "테스트 시작" 메시지 처리
        if message.strip() == "테스트 시작":
            result = quiz_bot.get_quiz(thread_id)
            if result.get('type') == 'QUIZ':
                # 퀴즈 정보를 전역 저장소에 저장
                current_quiz_store[thread_id] = result.get('quiz')
            print(f"\n=== 새 퀴즈 생성 ===\n{result}")
            return jsonify(result)
        
        # 답변 체크 (선택지 입력)
        answer_options = ["①", "②", "③", "④", "⑤", "1", "2", "3", "4", "5"]
        stripped_message = message.strip()
        
        # 답변에서 숫자만 추출
        if any(opt in stripped_message for opt in answer_options):
            print("\n=== 답변 체크 시작 ===")
            result = quiz_bot.check_answer(thread_id, stripped_message)
            print(f"\n=== 답변 결과 ===\n{result}")
            
            try:
                # 저장된 퀴즈 정보 가져오기
                current_quiz = current_quiz_store.get(thread_id, {})
                print(f"\n=== 현재 퀴즈 정보 ===\n스레드: {thread_id}\n퀴즈: {current_quiz}")
                
                # 답변 저장
                answer = Answer(
                    user_id=current_user.id,
                    unit=current_quiz.get('unit', '미분류'),
                    question=current_quiz.get('question', ''),
                    user_answer=stripped_message,
                    is_correct=result.get('answer', {}).get('correct', False)
                )
                db.session.add(answer)
                db.session.commit()
                print(f"\n=== 답변 저장 완료 ===\n단원: {answer.unit}\n정답여부: {answer.is_correct}")
                
            except Exception as e:
                print(f"\n=== 답변 저장 실패 ===\n{str(e)}")
                db.session.rollback()
            
            return jsonify({
                "type": "ANSWER",
                "answer": {
                    "correct": result.get('answer', {}).get('correct', False),
                    "explanation": result.get('answer', {}).get('explanation', '답변을 확인할 수 없습니다.')
                }
            })
        
        # 일반 질문 처리
        print("\n=== 일반 질문 처리 ===")
        result = quiz_bot.get_explanation(thread_id, message)
        return jsonify(result)
        
    except Exception as e:
        print(f"\n=== 오류 발생 ===\n{str(e)}")
        return jsonify({"error": str(e)}), 500

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

if __name__ == '__main__':
    app.debug = True
    app.run()