import hashlib
import secrets
from datetime import datetime

from extensions import db


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
        salt = secrets.token_hex(32)
        self.password_hash = f'{hashlib.sha256((password + salt).encode()).hexdigest()}:{salt}'

    def check_password(self, password):
        if ':' not in self.password_hash:
            return False
        stored_hash, salt = self.password_hash.split(':')
        return hashlib.sha256((password + salt).encode()).hexdigest() == stored_hash

    def __repr__(self):
        return f'<User {self.username}>'


class Discount(db.Model):
    __tablename__ = 'discount'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('product.id'), nullable=False)
    min_quantity = db.Column(db.Integer, nullable=False, default=1)
    max_quantity = db.Column(db.Integer, nullable=True)
    percent = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    product = db.relationship('Product', backref=db.backref('discounts', lazy='select', cascade='all, delete-orphan'))

    def __repr__(self):
        return f'<Discount {self.id} {self.percent}% from {self.min_quantity}>'

    def applies_to(self, quantity):
        if quantity < self.min_quantity:
            return False
        return self.max_quantity is None or quantity <= self.max_quantity

    def price_for(self, quantity, unit_price):
        if not self.applies_to(quantity):
            return float(unit_price)
        return round(float(unit_price) * (100 - self.percent) / 100, 2)

    @classmethod
    def best_for(cls, product_id, quantity):
        matches = [d for d in cls.query.filter_by(product_id=product_id).all() if d.applies_to(quantity)]
        return max(matches, key=lambda d: d.percent) if matches else None


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
        instance = cls.query.first() or cls(content=content)
        instance.content = content
        db.session.add(instance)
        db.session.commit()


class FooterContent(db.Model):
    __tablename__ = 'footer_content'

    FIELDS = {
        'brand_name': 'زعفران فلاحتی',
        'tagline': 'زعفران اصل فلاحتی با کیفیت برتر، از مزارع تا خانه شما — ارسال مطمئن به سراسر کشور.',
        'phone': '09123456789',
        'email': 'info@safferon-felahati.ir',
        'hours': 'شنبه تا پنجشنبه، ۹ صبح تا ۶ عصر',
        'copyright': 'تمامی حقوق محفوظ است.',
    }

    id = db.Column(db.Integer, primary_key=True)
    brand_name = db.Column(db.String(200), nullable=True)
    tagline = db.Column(db.Text, nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    email = db.Column(db.String(200), nullable=True)
    hours = db.Column(db.String(200), nullable=True)
    copyright = db.Column(db.String(300), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get_values(cls):
        instance = cls.query.first()
        values = {}
        for field, fallback in cls.FIELDS.items():
            stored = getattr(instance, field, None) if instance else None
            values[field] = stored.strip() if stored and stored.strip() else fallback
        return values

    @classmethod
    def save_values(cls, **values):
        instance = cls.query.first()
        if instance is None:
            instance = cls()
            db.session.add(instance)
        for field in cls.FIELDS:
            setattr(instance, field, (values.get(field) or '').strip() or None)
        db.session.commit()
