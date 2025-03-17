from database import db
from datetime import datetime
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(120), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_admin = db.Column(db.Boolean, default=False)  # 관리자 여부 추가
    answers = db.relationship('Answer', backref='user', lazy=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)
        
    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
        
    @property
    def password(self):
        raise AttributeError('password is not a readable attribute')
        
    @password.setter
    def password(self, password):
        self.set_password(password)

    def __repr__(self):
        return f'<User {self.username}>'

class Answer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    
    # 새로운 분류 체계
    subject = db.Column(db.String(50), nullable=True)     # 과목 (예: 과학, 수학 등)
    grade = db.Column(db.String(20), nullable=True)       # 학년 (예: 중1, 중2, 중3)
    unit = db.Column(db.String(100), nullable=True)       # 단원
    
    # 기존 필드 (하위 호환성 유지)
    main_unit = db.Column(db.String(100), nullable=True)  # 대단원 (이전 버전 호환용)
    sub_unit = db.Column(db.String(100), nullable=True)   # 소단원 (이전 버전 호환용)
    
    question = db.Column(db.Text, nullable=False)
    user_answer = db.Column(db.String(10), nullable=False)
    is_correct = db.Column(db.Boolean, nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

def init_db():
    db.create_all()