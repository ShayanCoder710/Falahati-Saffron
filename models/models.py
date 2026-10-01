import hashlib
import os
from extensions import db
from datetime import datetime
import uuid


def generate_token():
    return str(uuid.uuid4())


class Admin(db.Model):
    __tablename__ = 'admin'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def set_password(self, password):
        salt = os.urandom(32).hex()
        self.password_hash = hashlib.sha256(
            (password + salt).encode()
        ).hexdigest() + ':' + salt

    def check_password(self, password):
        if ':' not in self.password_hash:
            return False
        stored_hash, salt = self.password_hash.split(':')
        return hashlib.sha256((password + salt).encode()).hexdigest() == stored_hash


class Product(db.Model):
    __tablename__ = 'product'

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(100), nullable=True)
    name = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=True)
    price = db.Column(db.Numeric(10, 2), nullable=False)
    stock = db.Column(db.Integer, default=0)
    image = db.Column(db.String(500), nullable=True)
    category = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    orders = db.relationship('OrderItem', backref='product', lazy='select')

    def __repr__(self):
        return f'<Product {self.name}>'


class Order(db.Model):
    __tablename__ = 'order'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    customer_name = db.Column(db.String(200), nullable=False)
    customer_phone = db.Column(db.String(20), nullable=False)
    customer_address = db.Column(db.Text, nullable=False)
    total_price = db.Column(db.Numeric(10, 2), nullable=False)
    status = db.Column(db.String(50), default='pending')
    payment_ref = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    items = db.relationship('OrderItem', backref='order', lazy='select', cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Order {self.id}>'


class OrderItem(db.Model):
    __tablename__ = 'order_item'

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey('order.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=1)
    price = db.Column(db.Numeric(10, 2), nullable=False)

    def __repr__(self):
        return f'<OrderItem {self.id}>'


class User(db.Model):
    __tablename__ = 'user'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(256), nullable=False)
    full_name = db.Column(db.String(200), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    address = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    orders = db.relationship('Order', backref='user', lazy='select')

    def set_password(self, password):
        salt = os.urandom(32).hex()
        self.password_hash = hashlib.sha256(
            (password + salt).encode()
        ).hexdigest() + ':' + salt

    def check_password(self, password):
        if ':' not in self.password_hash:
            return False
        stored_hash, salt = self.password_hash.split(':')
        return hashlib.sha256((password + salt).encode()).hexdigest() == stored_hash


class AboutContent(db.Model):
    __tablename__ = 'about_content'

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get_content(cls):
        instance = cls.query.first()
        return instance.content if instance else ''

    @classmethod
    def set_content(cls, content):
        instance = cls.query.first()
        if instance:
            instance.content = content
        else:
            instance = cls()
            instance.content = content
            db.session.add(instance)
        db.session.commit()
