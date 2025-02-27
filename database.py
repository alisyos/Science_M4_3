from flask_sqlalchemy import SQLAlchemy
import os

# Flask-SQLAlchemy 인스턴스 생성
db = SQLAlchemy()

def init_db(app):
    instance_path = app.instance_path
    if not os.path.exists(instance_path):
        os.makedirs(instance_path)
        
    db_path = os.path.join(instance_path, 'quiz.db')
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