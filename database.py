import os
from flask_sqlalchemy import SQLAlchemy

# Flask-SQLAlchemy 인스턴스 생성
db = SQLAlchemy()

def init_db(app):
    # PostgreSQL URL 설정
    if os.environ.get('DATABASE_URL'):
        # render.com의 DATABASE_URL은 'postgres://'로 시작하지만
        # SQLAlchemy는 'postgresql://'을 사용
        database_url = os.environ.get('DATABASE_URL')
        if database_url.startswith('postgres://'):
            database_url = database_url.replace('postgres://', 'postgresql://', 1)
        app.config['SQLALCHEMY_DATABASE_URI'] = database_url
    else:
        # 로컬 개발용 SQLite
        db_path = os.path.join(app.instance_path, 'quiz.db')
        app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    
    with app.app_context():
        # 데이터베이스 테이블 생성
        db.create_all()
        print(f'데이터베이스 생성 완료: {db_path}')
        
        # admin 계정 생성
        from models import User
        admin = User.query.filter_by(username='admin').first()
        if not admin:
            admin = User(username='admin')
            admin.set_password('admin123')  # 비밀번호 해시 처리
            db.session.add(admin)
            db.session.commit()
            print('관리자 계정이 생성되었습니다.') 