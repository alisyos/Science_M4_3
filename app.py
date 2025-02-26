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
    def __init__(self):
        self.assistant_id = os.getenv('ASSISTANT_ID')
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
            
            # JSON 부분만 추출
            if '{' in response_text and '}' in response_text:
                json_text = response_text[response_text.find('{'):response_text.rfind('}')+1]
                result = json.loads(json_text)
                result['thread_id'] = thread_id
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
            
            # JSON 부분만 추출
            if '{' in response_text and '}' in response_text:
                json_text = response_text[response_text.find('{'):response_text.rfind('}')+1]
                return json.loads(json_text)
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