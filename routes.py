from flask import Flask, request, jsonify, render_template, current_app, send_from_directory, make_response
from openai import OpenAI
import json
import random
from datetime import datetime
from database import Base, db
from sqlalchemy import func, case, distinct
import os
import time
import logging
from flask_login import login_required
from models import User, Answer

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
            
            # GPT 응답 처리
            response = client.beta.threads.messages.list(thread_id=thread_id)
            response_text = response.data[0].content[0].text.value
            
            print("\n=== GPT 답변 ===")
            print(response_text)
            
            # JSON 파싱
            try:
                if '```json' in response_text:
                    json_text = response_text.split('```json')[1].split('```')[0].strip()
                else:
                    json_text = response_text.strip()
                    
                result = json.loads(json_text)
                
                # 일반 대화 응답 형식으로 변환
                return {
                    "type": "CHAT",
                    "message": result.get('answer', {}).get('explanation', response_text)
                }
                
            except json.JSONDecodeError:
                # JSON이 아닌 일반 텍스트 응답인 경우
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

def init_routes(app):
    @app.route('/')
    def quiz_page():
        return render_template('quiz.html')

    @app.route('/api/quiz/new', methods=['POST'])
    def new_quiz():
        try:
            quiz_bot = ScienceQuizBot()
            result = quiz_bot.get_quiz()
            return jsonify(result)
        except Exception as e:
            print(f"Error in new_quiz: {str(e)}")
            return jsonify({
                "error": "퀴즈를 생성하는 중 오류가 발생했습니다.",
                "details": str(e)
            }), 500

    @app.route('/api/chat', methods=['POST'])
    def chat():
        try:
            data = request.get_json()
            thread_id = data.get('thread_id')
            message = data.get('message')
            
            if not thread_id or not message:
                return jsonify({"error": "thread_id와 message는 필수입니다."}), 400
            
            quiz_bot = ScienceQuizBot()
            
            # "테스트 시작" 메시지 처리
            if message.strip() == "테스트 시작":
                result = quiz_bot.get_quiz(thread_id)
                return jsonify(result)
            
            # 답변이 선택지인 경우 (①~⑤ 또는 1~5)
            if message.strip() in ["①", "②", "③", "④", "⑤"] or message.strip() in ["1", "2", "3", "4", "5"]:
                result = quiz_bot.check_answer(thread_id, message)
                return jsonify(result)
            
            # 일반 질문에 대한 응답 처리
            result = quiz_bot.get_explanation(thread_id, message)
            return jsonify(result)
            
        except Exception as e:
            print(f"Chat error: {str(e)}")
            return jsonify({"error": str(e)}), 500

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

    @app.route('/admin/stats/unit-report')
    @login_required
    def download_unit_stats():
        student_id = request.args.get('student_id')
        
        # 단원별 통계 쿼리
        query = db.session.query(
            Answer.unit,
            func.count(Answer.id).label('attempts'),
            func.sum(case((Answer.is_correct, 1), else_=0)).label('correct'),
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
        # 학생별 통계 쿼리
        student_stats = db.session.query(
            User,
            func.count(Answer.id).label('total'),
            func.sum(case((Answer.is_correct, 1), else_=0)).label('correct')
        ).join(Answer).group_by(User.id).all()
        
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
        # 전체 통계 (단원별 + 학생별)
        unit_stats = db.session.query(
            Answer.unit,
            func.count(Answer.id).label('attempts'),
            func.sum(case((Answer.is_correct, 1), else_=0)).label('correct'),
            func.count(distinct(Answer.user_id)).label('unique_students')
        ).group_by(Answer.unit).all()
        
        student_stats = db.session.query(
            User,
            func.count(Answer.id).label('total'),
            func.sum(case((Answer.is_correct, 1), else_=0)).label('correct')
        ).join(Answer).group_by(User.id).all()
        
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

    return app