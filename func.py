import hashlib
import secrets


from extensions import db
from models.models import AboutContent, Discount, FooterContent

FOOTER_FIELDS = {
    'brand_name': 'زعفران فلاحتی',
    'tagline': 'زعفران اصل فلاحتی با کیفیت برتر، از مزارع تا خانه شما — ارسال مطمئن به سراسر کشور.',
    'phone': '09123456789',
    'email': 'info@safferon-felahati.ir',
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
    matches = [
        d for d in Discount.query.filter_by(product_id=product_id).all()
        if discount_applies(d, quantity)
    ]
    return max(matches, key=lambda d: d.percent) if matches else None


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
