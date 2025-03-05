# 과학 퀴즈 애플리케이션

AI 기반의 과학 퀴즈 학습 플랫폼입니다. 학생들의 학습 진도와 성취도를 추적하고 관리할 수 있습니다.

## 주요 기능

- AI 기반 과학 퀴즈 생성
- 학생별 학습 진도 추적
- 단원별 통계 분석
- 관리자 대시보드
- 통계 리포트 다운로드

## 설치 방법

1. 저장소 클론:
```bash
git clone [저장소_URL]
cd [프로젝트_디렉토리]
```

2. 가상환경 생성 및 활성화:
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
```

3. 필요한 패키지 설치:
```bash
pip install -r requirements.txt
```

4. 환경 변수 설정:
- `.env.example` 파일을 `.env`로 복사
- `.env` 파일에 실제 값들을 입력

5. 서버 실행:
```bash
python app.py
```

## 환경 설정

다음 환경 변수들이 필요합니다:

- `OPENAI_API_KEY`: OpenAI API 키
- `ASSISTANT_ID`: OpenAI Assistant ID
- `FLASK_SECRET_KEY`: Flask 비밀 키

## 기술 스택

- Python
- Flask
- SQLAlchemy
- OpenAI API
- Flask-Login 