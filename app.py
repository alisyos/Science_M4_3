from flask import Flask, request, jsonify, render_template, session, redirect, url_for
from openai import OpenAI
import json
import random
from datetime import datetime
from database import Base, db, init_db
from sqlalchemy import func, case
from models import QuizResult
import time
from dotenv import load_dotenv
import os

app = Flask(__name__)
load_dotenv()

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

init_db()

class ScienceQuizBot:
    _instance = None
    _thread_id = None  # 클래스 레벨에서 thread_id 캐싱

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.assistant_id = 'asst_ao5HoApasZzHhDRVlzTPq5za'
            self.initialized = True

    def get_or_create_thread(self):
        if not self._thread_id:
            thread = client.beta.threads.create()
            self._thread_id = thread.id
        return self._thread_id

    def get_quiz(self, thread_id=None):
        try:
            if not thread_id:
                thread_id = self.get_or_create_thread()

            print("\n=== 퀴즈 요청 ===")
            print("요청 내용: 새로운 문제를 출제해주세요.")
            
            message = client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content="새로운 문제를 출제해주세요."
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
            
            messages = client.beta.threads.messages.list(thread_id=thread_id)
            response_text = messages.data[0].content[0].text.value
            
            print("\n=== GPT 응답 ===")
            print(response_text)
            
            try:
                quiz_data = json.loads(response_text)
                return {
                    "thread_id": thread_id,
                    "quiz": quiz_data["quiz"]
                }
            except json.JSONDecodeError:
                print("\n=== JSON 파싱 오류 ===")
                print(f"Response text: {response_text}")
                return {
                    "error": "퀴즈 데이터 형식이 올바르지 않습니다."
                }
            
        except Exception as e:
            print("\n=== 오류 발생 ===")
            print(f"Error: {str(e)}")
            return {"error": str(e)}

    def check_answer(self, thread_id, answer):
        try:
            print("\n=== 답변 제출 ===")
            print(f"Thread ID: {thread_id}")
            print(f"사용자 답변: {answer}")
            
            message = client.beta.threads.messages.create(
                thread_id=thread_id,
                role="user",
                content=answer
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
            
            messages = client.beta.threads.messages.list(thread_id=thread_id)
            response_text = messages.data[0].content[0].text.value
            
            print("\n=== GPT 답변 평가 ===")
            print(response_text)
            
            try:
                response_data = json.loads(response_text)
                return response_data
            except json.JSONDecodeError:
                print("\n=== JSON 파싱 오류 ===")
                print(f"Response text: {response_text}")
                return {
                    "type": "ANSWER",
                    "answer": {
                        "correct": False,
                        "explanation": "죄송합니다. 응답을 처리하는 중 오류가 발생했습니다."
                    }
                }
            
        except Exception as e:
            print("\n=== 오류 발생 ===")
            print(f"Error: {str(e)}")
            return {
                "type": "ANSWER",
                "answer": {
                    "correct": False,
                    "explanation": "죄송합니다. 답변을 확인하는 중 오류가 발생했습니다."
                }
            }

@app.route('/')
def quiz_page():
    return render_template('quiz.html')

@app.route('/api/quiz/new', methods=['POST'])
def new_quiz():
    quiz_bot = ScienceQuizBot()
    thread = quiz_bot.get_or_create_thread()
    quiz_data = quiz_bot.get_quiz(thread)
    return jsonify(quiz_data)

@app.route('/api/quiz/answer', methods=['POST'])
def submit_answer():
    data = request.json
    thread_id = data.get('thread_id')
    answer = data.get('answer')
    
    quiz_bot = ScienceQuizBot()
    result = quiz_bot.check_answer(thread_id, answer)
    
    return jsonify(result)

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
            
        # 답변 체크
        result = quiz_bot.check_answer(thread_id, message)
        # 응답 형식 수정
        if result and isinstance(result, dict):
            return jsonify(result)  # 직접 result를 반환
        
        return jsonify({"error": "응답 형식이 올바르지 않습니다."}), 400
        
    except Exception as e:
        print(f"Chat error: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.debug = True
    app.run()