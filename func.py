import hashlib
import secrets

from datetime import datetime

from flask import session
from sqlalchemy import or_

from extensions import db
from models.models import AboutContent, Coupon, Discount, FooterContent, Order, Product

FOOTER_FIELDS = {
    'brand_name': 'زعفران فلاحتی',
    'tagline': 'زعفران اصل فلاحتی با کیفیت برتر، از مزارع تا خانه شما — ارسال مطمئن به سراسر کشور.',
    'phone': '09123456789',
    'email': 'info@safferon-falahati.ir',
    'hours': 'شنبه تا پنجشنبه، ۹ صبح تا ۶ عصر',
    'copyright': 'تمامی حقوق محفوظ است.',
}


def hash_password(password):
    salt = secrets.token_hex(32)
    return f'{hashlib.sha256((password + salt).encode()).hexdigest()}:{salt}'


def verify_password(password_hash, password):
    if ':' not in password_hash:
        return False
    stored_hash, salt = password_hash.split(':')
    return hashlib.sha256((password + salt).encode()).hexdigest() == stored_hash


def is_valid_phone(phone):
    digits = (phone or '').replace('+98', '').lstrip()
    if not digits.startswith('0') or not 10 <= len(digits) <= 11:
        return False
    return digits.isdigit()


def discount_applies(discount, quantity):
    if quantity < discount.min_quantity:
        return False
    return discount.max_quantity is None or quantity <= discount.max_quantity


def discount_price(discount, quantity, unit_price):
    if not discount_applies(discount, quantity):
        return float(unit_price)
    return round(float(unit_price) * (100 - discount.percent) / 100, 2)


def best_discount(product_id, quantity):
    if product_id is None:
        candidates = Discount.query.filter(Discount.product_id.is_(None)).all()
    else:
        candidates = Discount.query.filter(or_(
            Discount.product_id == product_id,
            Discount.product_id.is_(None),
        )).all()
    matches = [d for d in candidates if discount_applies(d, quantity)]
    return max(matches, key=lambda d: d.percent) if matches else None


def normalize_coupon(raw):
    return (raw or '').strip().upper()


def parse_coupon_date(raw):
    parts = (raw or '').strip().split('-')
    if len(parts) != 3:
        return None
    try:
        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        return datetime(year, month, day, 23, 59, 59)
    except ValueError:
        return None


def coupon_blocked(coupon):
    if coupon is None:
        return 'کد تخفیف یافت نشد'
    if coupon.expires_at and coupon.expires_at < datetime.utcnow():
        return 'اعتبار این کد تخفیف به پایان رسیده است'
    if coupon.max_uses is not None and coupon.used_count >= coupon.max_uses:
        return 'سقف استفاده از این کد تخفیف تکمیل شده است'
    return None


def find_coupon(raw):
    code = normalize_coupon(raw)
    if not code:
        return None
    return Coupon.query.filter_by(code=code).first()


def active_coupon():
    coupon = find_coupon(session.get('coupon'))
    return None if coupon_blocked(coupon) else coupon


def set_coupon(raw):
    coupon = find_coupon(raw)
    blocked = coupon_blocked(coupon)
    if blocked:
        clear_coupon()
        return None, blocked
    session['coupon'] = coupon.code
    session.modified = True
    return coupon, None


def clear_coupon():
    session.pop('coupon', None)
    session.modified = True


def consume_coupon(order):
    coupon = find_coupon(order.coupon_code)
    if coupon is None:
        return
    coupon.used_count = (coupon.used_count or 0) + 1


def release_coupon(order):
    coupon = find_coupon(order.coupon_code)
    if coupon is None or not coupon.used_count:
        return
    coupon.used_count -= 1


def unit_price_for(product, quantity, coupon_percent=0):
    discount = best_discount(product.id, quantity)
    base = float(product.price)
    tier_percent = discount.percent if discount else 0
    winner = max(tier_percent, coupon_percent)
    if winner == 0:
        return base, None
    return round(base * (100 - winner) / 100, 2), winner


def about_content():
    instance = AboutContent.query.first()
    return instance.content if instance else ''


def save_about_content(content):
    instance = AboutContent.query.first() or AboutContent(content=content)
    instance.content = content
    db.session.add(instance)
    db.session.commit()


def footer_values():
    instance = FooterContent.query.first()
    values = {}
    for field, fallback in FOOTER_FIELDS.items():
        stored = getattr(instance, field, None) if instance else None
        values[field] = stored.strip() if stored and stored.strip() else fallback
    return values


def save_footer_values(**values):
    instance = FooterContent.query.first()
    if instance is None:
        instance = FooterContent()
        db.session.add(instance)
    for field in FOOTER_FIELDS:
        setattr(instance, field, (values.get(field) or '').strip() or None)
    db.session.commit()


def escape_html(value):
    return (
        str(value)
        .replace('&', '&amp;')
        .replace('<', '&lt;')
        .replace('>', '&gt;')
        .replace('"', '&quot;')
    )


def data_html(**values):
    pairs = [f'data-{key.replace("_", "-")}="{escape_html(value)}"' for key, value in values.items()]
    return '<div ' + ' '.join(pairs) + '></div>'


def parse_int(raw, default=None):
    value = (raw or '').strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return None


def parse_number(raw, default=None):
    value = (raw or '').strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return None


def search_products(keyword):
    if not keyword:
        return Product.query.all()
    like = f'%{keyword}%'
    return Product.query.filter(or_(
        Product.name.like(like),
        Product.description.like(like),
        Product.category.like(like),
        Product.code.like(like),
    )).all()


def get_cart():
    if 'cart' not in session:
        session['cart'] = []
        session.modified = True
    return session['cart']


def get_cart_details():
    details, total = [], 0
    coupon = active_coupon()
    coupon_percent = coupon.percent if coupon else 0
    for entry in get_cart():
        product = db.session.get(Product, entry['product_id'])
        if not product:
            continue
        quantity = min(int(entry['quantity']), product.stock or 0)
        if quantity < 1:
            continue
        discount = best_discount(product.id, quantity)
        unit_price, winner = unit_price_for(product, quantity, coupon_percent)
        details.append({
            'product': product,
            'quantity': quantity,
            'max_qty': product.stock or 0,
            'unit_price': unit_price,
            'discount': discount,
            'coupon': coupon,
            'winner_percent': winner,
            'saved': round(float(product.price) - unit_price, 2) * quantity,
        })
        total += unit_price * quantity
    return details, total


def get_user_orders(user_id):
    orders = []
    for order in Order.query.filter_by(user_id=user_id).order_by(Order.created_at.desc()).all():
        lines = [
            f'{item.product.name if item.product else f"محصول #{item.product_id}"} × {item.quantity}'
            for item in order.items
        ]
        orders.append({
            'id': order.id,
            'total': order.total_price,
            'status': order.status,
            'created_at': order.created_at,
            'lines': lines,
        })
    return orders


def order_stock_available(order):
    db.session.expire_all()
    return all((item.product.stock or 0) >= item.quantity for item in order.items)


def change_stock(order, amount):
    for item in order.items:
        if item.product:
            item.product.stock = (item.product.stock or 0) + amount * item.quantity


def fa_num(value, decimals=0):
    try:
        text = f'{float(value):,.{decimals}f}'
    except (TypeError, ValueError):
        text = str(value)
    return text.translate(str.maketrans('0123456789,', '۰۱۲۳۴۵۶۷۸۹٬'))


def fa_digits(value):
    if value is None:
        return ''
    return str(value).translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))


def fa_date(value, fmt='%Y/%m/%d'):
    if not value:
        return '-'
    return fa_digits(value.strftime(fmt))
