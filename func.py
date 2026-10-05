import hashlib
import secrets

from datetime import date, datetime

from flask import session
from sqlalchemy import or_

import re

from extensions import db
from models.models import AboutContent, Coupon, Discount, FooterContent, Order, Product

ALLOWED_TAGS = {
    'p', 'br', 'b', 'strong', 'i', 'em', 'u', 'ul', 'ol', 'li', 'h1', 'h2',
    'h3', 'h4', 'blockquote', 'a', 'span', 'div', 'hr',
}


def strip_dangerous_html(text):
    cleaned = re.sub(r'(?is)<\s*(script|style|iframe|object|embed|link|meta)'
                     r'[^>]*>.*?<\s*/\s*\1\s*>', '', text or '')
    cleaned = re.sub(r'(?is)<\s*(script|style|iframe|object|embed|link|meta)'
                     r'[^>]*/?>', '', cleaned)

    def clean_tag(match):
        tag = match.group(0)
        tag = re.sub(r'(?is)\s+on[a-z]+\s*=\s*("[^"]*"|\'[^\']*\'|[^\s>]+)',
                     '', tag)
        tag = re.sub(r'(?is)\s+(href|src)\s*=\s*("javascript:[^"]*"'
                     r"|'javascript:[^']*'|javascript:[^\s>]+)", '', tag)
        return tag

    cleaned = re.sub(r'(?is)<[^>]+>', clean_tag, cleaned)

    def filter_tag(match):
        name = match.group(2).lower()
        if name not in ALLOWED_TAGS:
            return ''
        return match.group(0)

    cleaned = re.sub(r'(?is)<\s*(/?)\s*([a-z0-9]+)(?=[\s/>])[^>]*>',
                     filter_tag, cleaned)
    return cleaned

FOOTER_FIELDS = {
    'brand_name': 'زعفران فلاحتی',
    'tagline': 'زعفران اصل فلاحتی با کیفیت برتر، از مزارع تا خانه شما — ارسال مطمئن به سراسر کشور.',
    'phone': '09123456789',
    'email': 'info@safferon-falahati.ir',
    'hours': 'شنبه تا پنجشنبه، ۹ صبح تا ۶ عصر',
    'copyright': 'تمامی حقوق محفوظ است.',
}


JALALI_MONTHS = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
                  'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند']

JALALI_LEAP = (1, 5, 9, 13, 17, 22, 26, 30)

JALALI_EPOCH_ORDINAL = 226895

GREGORIAN_DAYS = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]


def jalali_is_leap(jy):
    return jy % 33 in JALALI_LEAP


def jalali_days_in_month(jy, jm):
    if jm < 1 or jm > 12:
        return 0
    if jm <= 6:
        return 31
    if jm <= 11:
        return 30
    return 30 if jalali_is_leap(jy) else 29


def jalali_valid(jy, jm, jd):
    return jy >= 1 and 1 <= jm <= 12 \
        and 1 <= jd <= jalali_days_in_month(jy, jm)


def gregorian_to_jalali(gy, gm, gd):
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 \
        + (gy2 + 399) // 400 - 80 + gd + GREGORIAN_DAYS[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        return jy, 1 + days // 31, 1 + days % 31
    return jy, 7 + (days - 186) // 30, 1 + (days - 186) % 30


def jalali_to_gregorian(jy, jm, jd):
    cycles, rest = divmod(jy - 1, 33)
    days = cycles * 12053 + rest * 365
    for year in range(jy - rest, jy):
        if jalali_is_leap(year):
            days += 1
    for month in range(1, jm):
        days += jalali_days_in_month(jy, month)
    value = date.fromordinal(JALALI_EPOCH_ORDINAL + days + jd - 1)
    return value.year, value.month, value.day


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


def is_valid_username(username):
    if not 3 <= len(username or '') <= 80:
        return False
    return username.replace('_', '').replace('.', '').isalnum()


def discount_applies(discount, quantity):
    if quantity < discount.min_quantity:
        return False
    return discount.max_quantity is None or quantity <= discount.max_quantity


def discount_price(discount, quantity, unit_price):
    if not discount_applies(discount, quantity):
        return float(unit_price)
    return round(float(unit_price) * (100 - discount.percent) / 100, 2)


def per_product_discount(product_id, quantity):
    candidates = Discount.query.filter(
        Discount.product_id == product_id).all()
    matches = [d for d in candidates if discount_applies(d, quantity)]
    return max(matches, key=lambda d: d.percent) if matches else None


def active_global_discount():
    return Discount.query.filter(Discount.product_id.is_(None)).first()


def coupon_covers(coupon_percent, items, coupon):
    if not coupon_percent:
        return [False] * len(items)
    limit = getattr(coupon, 'item_limit', None) if coupon else None
    if not limit:
        return [True] * len(items)
    flags, used = [], 0
    for item in items:
        if item.get('discount_percent', 0) >= coupon_percent:
            flags.append(False)
            continue
        room = limit - used
        if room <= 0:
            flags.append(False)
            continue
        flags.append(item['quantity'] <= room)
        if item['quantity'] <= room:
            used += item['quantity']
    return flags


def safe_redirect_target(raw, fallback):
    value = (raw or '').strip()
    if not value.startswith('/'):
        return fallback
    if value.startswith('//') or value.startswith('/\\'):
        return fallback
    if '\\' in value or '\n' in value or '\r' in value or '\t' in value:
        return fallback
    return value


def normalize_coupon(raw):
    return (raw or '').strip().upper()


def parse_jalali_date(raw):
    text = (raw or '').strip().translate(
        str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))
    parts = text.replace('/', '-').split('-')
    if len(parts) != 3:
        return None
    try:
        jy, jm, jd = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if not jalali_valid(jy, jm, jd):
        return None
    gy, gm, gd = jalali_to_gregorian(jy, jm, jd)
    return datetime(gy, gm, gd, 23, 59, 59)


def coupon_blocked(coupon):
    if coupon is None:
        return 'کد تخفیف یافت نشد'
    if coupon.expires_at and coupon.expires_at < datetime.now():
        return 'اعتبار این کد تخفیف به پایان رسیده است'
    if coupon.used_count >= coupon.max_uses:
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
    if coupon is None or order.coupon_consumed:
        return
    coupon.used_count = (coupon.used_count or 0) + 1
    order.coupon_consumed = True


def release_coupon(order):
    coupon = find_coupon(order.coupon_code)
    if coupon is None or not order.coupon_consumed:
        return
    coupon.used_count = max(0, (coupon.used_count or 0) - 1)
    order.coupon_consumed = False


def unit_price_for(product, quantity, coupon_percent=0):
    discount = per_product_discount(product.id, quantity)
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
    safe = strip_dangerous_html(content)
    instance = AboutContent.query.first() or AboutContent(content=safe)
    instance.content = safe
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


INVALID = object()


def parse_int(raw, default=None, minimum=None, maximum=None):
    value = (raw or '').strip()
    if not value:
        return default if default is not None else INVALID
    if len(value) > 12:
        return INVALID
    try:
        number = int(value)
    except ValueError:
        return INVALID
    if minimum is not None and number < minimum:
        return INVALID
    if maximum is not None and number > maximum:
        return INVALID
    return number


def parse_number(raw, default=None):
    value = (raw or '').strip()
    if not value:
        return default
    if len(value) > 20:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def clean_text(raw, maximum):
    value = (raw or '').strip()
    if len(value) > maximum:
        return None
    return value


def optional_text(raw, maximum):
    value = clean_text(raw, maximum)
    if not value:
        return None
    if value.lower() in ('none', 'null', 'undefined', 'nan', 'false'):
        return None
    return value


def parse_optional_int(raw, minimum=None, maximum=None):
    value = (raw or '').strip()
    if not value:
        return None
    return parse_int(value, None, minimum=minimum, maximum=maximum)


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
    coupon = active_coupon()
    coupon_percent = coupon.percent if coupon else 0
    global_discount = active_global_discount()
    lines = []
    for entry in get_cart():
        product = db.session.get(Product, entry['product_id'])
        if not product:
            continue
        quantity = min(int(entry['quantity']), product.stock or 0)
        if quantity < 1:
            continue
        discount = per_product_discount(product.id, quantity)
        tier_percent = discount.percent if discount else 0
        lines.append({
            'product': product,
            'quantity': quantity,
            'max_qty': product.stock or 0,
            'discount': discount,
            'discount_percent': tier_percent,
        })

    flags = coupon_covers(coupon_percent, lines, coupon)
    total, used_items = 0, 0
    details = []
    for index, line in enumerate(lines):
        product = line['product']
        quantity = line['quantity']
        base = float(product.price)
        tier_percent = line['discount_percent']
        winner = max(tier_percent, coupon_percent if flags[index] else 0)
        if global_discount and global_discount.percent > winner:
            limit = global_discount.max_items
            room = (limit - used_items) if limit else 0
            if not limit or quantity <= room:
                winner = global_discount.percent
                if limit:
                    used_items += quantity
        percent = winner
        unit_price = round(base * (100 - percent) / 100, 2)
        applied_global = None
        if global_discount and percent == global_discount.percent and percent:
            applied_global = global_discount
        details.append({
            'product': product,
            'quantity': quantity,
            'max_qty': product.stock or 0,
            'unit_price': unit_price,
            'discount': line['discount'],
            'coupon': coupon if flags[index] else None,
            'winner_percent': percent or None,
            'global_discount': applied_global,
            'saved': round(base - unit_price, 2) * quantity,
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
    if isinstance(value, datetime):
        jy, jm, jd = gregorian_to_jalali(value.year, value.month, value.day)
        text = f'{jy}/{jm:02d}/{jd:02d}'
        if '%S' in fmt:
            text += ' ' + value.strftime('%H:%M:%S')
        elif '%H' in fmt or '%M' in fmt:
            text += ' ' + value.strftime('%H:%M')
        return fa_digits(text)
    if isinstance(value, date):
        jy, jm, jd = gregorian_to_jalali(value.year, value.month, value.day)
        return fa_digits(f'{jy}/{jm:02d}/{jd:02d}')
    return fa_digits(str(value))
