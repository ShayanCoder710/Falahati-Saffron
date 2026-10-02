import re
import uuid
from functools import wraps
from pathlib import Path

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from sqlalchemy import func, or_

from config import *
from extensions import db, csrf
from models.models import AboutContent, Discount, FooterContent, Order, OrderItem, Product, User

STATUS_LABELS = {
    'pending': 'در انتظار پرداخت',
    'paid': 'پرداخت شده',
    'shipped': 'ارسال شده',
    'completed': 'تکمیل شده',
    'cancelled': 'لغو شده',
}

STATUS_ICONS = {
    'pending': '⏳',
    'paid': '✓',
    'shipped': '📦',
    'completed': '🏁',
    'cancelled': '✕',
}

VALID_STATUSES = list(STATUS_LABELS)

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}

USERNAME_TAKEN = 'این نام کاربری قبلاً ثبت شده است'

INVALID_NUMBER = 'قیمت و موجودی باید عدد معتبر و مثبت باشد'


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = SECRET_KEY
    app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
    app.config['UPLOAD_FOLDER'] = str(Path(app.root_path) / 'static' / 'images')

    db.init_app(app)
    csrf.init_app(app)

    with app.app_context():
        db.create_all()

    def save_product_image(file_storage):
        if not file_storage or not file_storage.filename:
            return None
        suffix = Path(file_storage.filename).suffix.lower()
        if suffix not in IMAGE_EXTENSIONS:
            flash('نوع فایل عکس مجاز نیست', 'danger')
            return None
        folder = Path(app.config['UPLOAD_FOLDER'])
        folder.mkdir(parents=True, exist_ok=True)
        filename = f'{uuid.uuid4().hex}{suffix}'
        file_storage.save(folder / filename)
        return filename

    def delete_product_image(filename):
        path = Path(app.config['UPLOAD_FOLDER']) / filename
        if path.is_file():
            path.unlink()

    def parse_int(raw, default=None):
        value = (raw or '').strip()
        if not value:
            return default
        try:
            return int(value)
        except ValueError:
            return None

    def parse_number(form, field, default=None):
        raw = (form.get(field) or '').strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            return None

    def admin_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'admin_id' not in session:
                return redirect(url_for('admin_login'))
            return f(*args, **kwargs)
        return decorated

    def login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('user_login'))
            return f(*args, **kwargs)
        return decorated

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
        for entry in get_cart():
            product = db.session.get(Product, entry['product_id'])
            if not product:
                continue
            quantity = min(int(entry['quantity']), product.stock or 0)
            discount = Discount.best_for(product.id, quantity)
            unit_price = discount.price_for(quantity, product.price) if discount else float(product.price)
            details.append({
                'product': product,
                'quantity': quantity,
                'max_qty': product.stock or 0,
                'unit_price': unit_price,
                'discount': discount,
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

    @app.context_processor
    def inject_footer():
        return {'footer': FooterContent.get_values()}

    @app.context_processor
    def inject_globals():
        cart = session.get('cart') or []
        return {
            'status_labels': STATUS_LABELS,
            'status_icons': STATUS_ICONS,
            'cart_count': sum(int(i.get('quantity', 0)) for i in cart),
        }

    @app.template_filter('fa_num')
    def fa_num(value, decimals=0):
        try:
            text = f'{float(value):,.{decimals}f}'
        except (TypeError, ValueError):
            text = str(value)
        return text.translate(str.maketrans('0123456789,', '۰۱۲۳۴۵۶۷۸۹٬'))

    @app.template_filter('fa_digits')
    def fa_digits(value):
        if value is None:
            return ''
        return str(value).translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))

    @app.template_filter('fa_date')
    def fa_date(value, fmt='%Y/%m/%d'):
        if not value:
            return '-'
        return fa_digits(value.strftime(fmt))

    @app.route('/')
    def home():
        keyword = (request.args.get('q') or '').strip()
        return render_template('home.html', products=search_products(keyword), q=keyword)

    @app.route('/api/username-available', methods=['GET'])
    def username_available():
        username = (request.args.get('username') or '').strip()
        ignore_id = request.args.get('ignore_id', type=int)
        available = False
        if len(username) >= 3:
            query = User.query.filter(func.lower(User.username) == username.lower())
            if ignore_id:
                query = query.filter(User.id != ignore_id)
            available = query.first() is None
        return jsonify(available=available, checked=username)

    @app.route('/api/products', methods=['GET'])
    def products_api():
        keyword = (request.args.get('q') or '').strip()
        products = search_products(keyword)
        grid = render_template('_product_grid.html', products=products, q=keyword)
        return jsonify(count=len(products), q=keyword, html=grid)

    @app.route('/about')
    def about():
        return render_template('about.html', content=AboutContent.get_content())

    @app.route('/product/<int:pid>')
    def product_detail(pid):
        product = db.session.get(Product, pid)
        if product is None:
            abort(404)
        return render_template('product_detail.html', product=product)

    @app.route('/cart', methods=['GET', 'POST'])
    def cart():
        if request.method == 'POST':
            return add_to_cart()
        details, total = get_cart_details()
        orders = []
        if session.get('user_id'):
            orders = get_user_orders(session['user_id'])
        return render_template(
            'cart.html',
            items=details,
            total=total,
            my_orders=orders,
            my_orders_total=sum(float(o['total']) for o in orders if o['status'] != 'cancelled'),
            my_orders_active=len([o for o in orders if o['status'] != 'cancelled']),
        )

    def add_to_cart():
        product_id = request.form.get('product_id')
        try:
            quantity = max(1, int(request.form.get('quantity', 1) or 1))
        except (TypeError, ValueError):
            quantity = 1
        product = db.session.get(Product, product_id)
        if not product:
            flash('محصول یافت نشد', 'danger')
            return redirect(url_for('home'))
        items = get_cart()
        in_cart = next((i['quantity'] for i in items if str(i['product_id']) == str(product_id)), 0)
        stock = product.stock or 0
        if in_cart + quantity > stock:
            flash(f'موجودی این محصول تنها {stock} عدد است — می‌توانید حداکثر {max(stock - in_cart, 0)} عدد دیگر اضافه کنید', 'danger')
            return redirect(url_for('cart'))
        if in_cart:
            for item in items:
                if str(item['product_id']) == str(product_id):
                    item['quantity'] += quantity
                    break
        else:
            items.append({'product_id': product_id, 'quantity': quantity})
        session['cart'] = items
        session.modified = True
        flash('محصول به سبد اضافه شد', 'success')
        return redirect(url_for('cart'))

    @app.route('/cart/update/<int:pid>', methods=['POST'])
    def cart_update(pid):
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        items = get_cart()
        match = next((i for i in items if str(i['product_id']) == str(pid)), None)
        product = db.session.get(Product, pid)
        stock = (product.stock or 0) if product else 0
        try:
            requested = int(request.form.get('quantity', 1) or 1)
        except (TypeError, ValueError):
            requested = 1
        removed = False
        if match is None and requested > 0:
            items.append({'product_id': pid, 'quantity': requested})
            session['cart'] = items
            session.modified = True
        elif match is not None:
            if stock <= 0:
                items.remove(match)
                removed = True
                if not is_ajax:
                    flash('این محصول دیگر موجود نیست؛ از سبد حذف شد', 'danger')
            else:
                quantity = min(requested, stock)
                if not is_ajax and requested > stock:
                    flash(f'موجودی این محصول {stock} عدد است؛ تعداد به حداکثر اصلاح شد', 'danger')
                match['quantity'] = max(1, quantity)
                session['cart'] = items
                session.modified = True
        if is_ajax:
            details, total = get_cart_details()
            current = None if removed or not stock else max(1, min(requested, stock))
            discount = Discount.best_for(pid, current) if current else None
            unit_price = discount.price_for(current, product.price) if discount else (float(product.price) if product else 0)
            return jsonify(
                ok=True, removed=removed, total=total, quantity=current,
                count=sum(i['quantity'] for i in details),
                unit_price=unit_price,
                percent=discount.percent if discount else 0,
            )
        return redirect(url_for('cart'))

    @app.route('/cart/remove/<int:pid>', methods=['POST'])
    def cart_remove(pid):
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        session['cart'] = [i for i in get_cart() if str(i['product_id']) != str(pid)]
        session.modified = True
        if is_ajax:
            details, total = get_cart_details()
            return jsonify(ok=True, total=total, count=sum(i['quantity'] for i in details))
        return redirect(url_for('cart'))

    def order_stock_available(order):
        db.session.expire_all()
        return all(
            (item.product.stock or 0) >= item.quantity
            for item in order.items
        )

    def change_stock(order, amount):
        for item in order.items:
            if item.product:
                item.product.stock = (item.product.stock or 0) + amount * item.quantity

    def cancel_order(order, message):
        order.status = 'cancelled'
        db.session.commit()
        flash(message, 'danger')
        return redirect(url_for('home'))

    @app.route('/checkout', methods=['GET', 'POST'])
    @login_required
    def checkout():
        if not get_cart():
            flash('سبد خرید خالی است', 'danger')
            return redirect(url_for('cart'))
        details, total = get_cart_details()
        user = db.session.get(User, session['user_id'])
        if request.method != 'POST':
            return render_template('checkout.html', items=details, total=total, user=user)
        name = (request.form.get('name') or '').strip()
        phone = (request.form.get('phone') or '').strip()
        address = (request.form.get('address') or '').strip()
        if not all([name, phone, address]):
            flash('لطفا تمام فیلدها را پر کنید', 'danger')
        elif len(name) < 3 or len(address) < 5:
            flash('نام و آدرس را کامل وارد کنید', 'danger')
        elif not re.fullmatch(r'0\d{9,10}', phone.replace('+98', '').lstrip()):
            flash('شماره تماس معتبر نیست (مثال: 09123456789)', 'danger')
        elif any((item['product'].stock or 0) < item['quantity'] for item in details):
            flash('موجودی برخی محصولات کافی نیست، تعداد را در سبد خرید کاهش دهید', 'danger')
            return redirect(url_for('cart'))
        else:
            order = Order(
                user_id=session['user_id'],
                customer_name=name,
                customer_phone=phone,
                customer_address=address,
                total_price=total,
                status='pending',
                payment_ref=str(uuid.uuid4()),
            )
            db.session.add(order)
            db.session.flush()
            for item in details:
                db.session.add(OrderItem(
                    order_id=order.id,
                    product_id=item['product'].id,
                    quantity=item['quantity'],
                    price=item['unit_price'],
                ))
            db.session.commit()
            session.pop('cart', None)
            return redirect(url_for('payment_gateway', oid=order.id))
        return render_template('checkout.html', items=details, total=total, user=user)

    @app.route('/payment/<int:oid>')
    def payment_gateway(oid):
        order = db.session.get(Order, oid)
        if order is None:
            abort(404)
        if order.status == 'paid':
            return redirect(url_for('order_success', oid=order.id))
        if order.status == 'cancelled':
            flash('این سفارش لغو شده است', 'danger')
            return redirect(url_for('home'))
        if not order_stock_available(order):
            return cancel_order(order, 'موجودی برخی اقلام کافی نیست و سفارش لغو شد؛ دوباره خریداری کنید')
        return render_template('payment.html', order=order)

    @app.route('/payment/verify', methods=['POST'])
    @login_required
    def payment_verify():
        reference = request.form.get('payment_ref')
        order = Order.query.filter_by(payment_ref=reference).first()
        if order is None:
            abort(404)
        if order.status != 'pending':
            flash('این سفارش هنوز قابل پرداخت نیست', 'danger')
            return redirect(url_for('order_success', oid=order.id))
        if request.form.get('result') == 'fail':
            return cancel_order(order, 'پرداخت ناموفق بود و سفارش لغو شد')
        if not order_stock_available(order):
            return cancel_order(order, 'موجودی برخی اقلام کافی نیست و سفارش لغو شد')
        order.status = 'paid'
        change_stock(order, -1)
        db.session.commit()
        flash('پرداخت با موفقیت انجام شد', 'success')
        return redirect(url_for('order_success', oid=order.id))

    @app.route('/order/<int:oid>')
    @login_required
    def order_success(oid):
        order = db.session.get(Order, oid)
        if order is None:
            abort(404)
        if order.user_id != session['user_id']:
            flash('شما مجوز مشاهده این سفارش را ندارید', 'danger')
            return redirect(url_for('profile'))
        items = [{
            'name': item.product.name if item.product else f'محصول #{item.product_id}',
            'quantity': item.quantity,
            'price': item.price,
        } for item in order.items]
        return render_template(
            'order_success.html',
            order=order,
            items=items,
            pending=order.status == 'pending',
            failed=order.status == 'cancelled',
        )

    @app.route('/order/<int:oid>/pay')
    @login_required
    def order_pay(oid):
        order = db.session.get(Order, oid)
        if order is None:
            abort(404)
        if order.user_id != session['user_id'] or order.status != 'pending':
            flash('این سفارش قابل پرداخت نیست', 'danger')
            return redirect(url_for('order_success', oid=order.id))
        return redirect(url_for('payment_gateway', oid=order.id))

    @app.route('/register', methods=['GET', 'POST'])
    def user_register():
        if request.method != 'POST':
            return render_template('user_register.html')
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password', '')
        full_name = (request.form.get('full_name') or '').strip()
        phone = (request.form.get('phone') or '').strip()
        address = (request.form.get('address') or '').strip()
        if not username or not password:
            flash('نام کاربری و رمز عبور الزامی است', 'danger')
        elif password != request.form.get('password_confirm', ''):
            flash('رمز عبور و تکرار آن مطابقت ندارد', 'danger')
        elif User.query.filter(func.lower(User.username) == username.lower()).first():
            flash(USERNAME_TAKEN, 'danger')
        else:
            user = User(username=username, full_name=full_name, phone=phone, address=address)
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            session['user_id'] = user.id
            session.modified = True
            flash('ثبت‌نام با موفقیت انجام شد', 'success')
            return redirect(url_for('home'))
        return render_template('user_register.html')

    @app.route('/login', methods=['GET', 'POST'])
    def user_login():
        if request.method == 'POST':
            user = User.query.filter_by(username=(request.form.get('username') or '').strip()).first()
            if user and user.check_password(request.form.get('password', '')):
                session['user_id'] = user.id
                session.modified = True
                flash('ورود موفقیت آمیز بود', 'success')
                return redirect(url_for('profile'))
            flash('نام کاربری یا رمز عبور اشتباه است', 'danger')
        return render_template('user_login.html')

    @app.route('/logout')
    def user_logout():
        session.pop('user_id', None)
        session.modified = True
        flash('از حساب خود خارج شدید', 'info')
        return redirect(url_for('home'))

    @app.route('/profile', methods=['GET', 'POST'])
    @login_required
    def profile():
        user = db.session.get(User, session['user_id'])
        orders = user.orders
        context = {
            'user': user,
            'active_orders': len([o for o in orders if o.status != 'cancelled']),
            'pending_orders': len([o for o in orders if o.status == 'pending']),
        }
        if request.method != 'POST':
            return render_template('profile.html', **context)
        new_username = (request.form.get('username') or '').strip()
        if not new_username:
            flash('نام کاربری نمی‌تواند خالی باشد', 'danger')
            return render_template('profile.html', **context)
        taken = User.query.filter(
            func.lower(User.username) == new_username.lower(),
            User.id != user.id,
        ).first()
        if taken:
            flash('این نام کاربری توسط کاربر دیگری در استفاده است', 'danger')
            return render_template('profile.html', **context)
        user.username = new_username
        user.full_name = (request.form.get('full_name') or '').strip()
        user.phone = (request.form.get('phone') or '').strip()
        user.address = (request.form.get('address') or '').strip()
        db.session.commit()
        flash('اطلاعات حساب به‌روز شد', 'success')
        return redirect(url_for('profile'))

    @app.route('/admin/login', methods=['GET', 'POST'])
    def admin_login():
        if request.method == 'POST':
            username = request.form.get('username')
            password = request.form.get('password')
            if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
                session['admin_id'] = 1
                session.modified = True
                flash('ورود موفقیت آمیز بود', 'success')
                return redirect(url_for('admin_dashboard'))
            flash('نام کاربری یا رمز عبور اشتباه است', 'danger')
        return render_template('admin/login.html')

    @app.route('/admin/logout')
    def admin_logout():
        session.pop('admin_id', None)
        session.modified = True
        flash('خروج از پنل ادمین', 'info')
        return redirect(url_for('admin_login'))

    @app.route('/admin')
    @admin_required
    def admin_dashboard():
        return render_template(
            'admin/dashboard.html',
            products_count=Product.query.count(),
            orders_count=Order.query.count(),
            users_count=User.query.count(),
            pending_orders=Order.query.filter_by(status='pending').count(),
            paid_orders=Order.query.filter_by(status='paid').count(),
            recent_orders=Order.query.order_by(Order.created_at.desc()).limit(10).all(),
            low_stock_products=Product.query.filter(Product.stock < 5).all(),
        )

    @app.route('/admin/products')
    @admin_required
    def admin_products():
        return render_template('admin/products.html', products=Product.query.all())

    @app.route('/admin/products/add', methods=['GET', 'POST'])
    @admin_required
    def admin_product_add():
        if request.method == 'POST':
            name = (request.form.get('name') or '').strip()
            price = parse_number(request.form, 'price')
            stock = parse_number(request.form, 'stock', 0)
            if not name or not price or price < 0:
                flash('نام و قیمت محصول الزامی است', 'danger')
                return render_template('admin/product_form.html')
            if stock is None or stock < 0:
                flash(INVALID_NUMBER, 'danger')
                return render_template('admin/product_form.html')
            product = Product(
                code=request.form.get('code', ''),
                name=name,
                description=request.form.get('description', ''),
                price=price,
                stock=int(stock),
                category=request.form.get('category', ''),
                image=save_product_image(request.files.get('image')),
            )
            db.session.add(product)
            db.session.commit()
            flash('محصول با موفقیت اضافه شد', 'success')
            return redirect(url_for('admin_products'))
        return render_template('admin/product_form.html')

    @app.route('/admin/products/edit/<int:pid>', methods=['GET', 'POST'])
    @admin_required
    def admin_product_edit(pid):
        product = db.session.get(Product, pid)
        if product is None:
            abort(404)
        if request.method == 'POST':
            price = parse_number(request.form, 'price')
            stock = parse_number(request.form, 'stock', 0)
            if not price or price < 0 or stock is None or stock < 0:
                flash(INVALID_NUMBER, 'danger')
                return render_template('admin/product_form.html', product=product)
            product.code = request.form.get('code', '')
            product.name = request.form.get('name')
            product.description = request.form.get('description', '')
            product.price = price
            product.stock = int(stock)
            product.category = request.form.get('category', '')
            new_image = save_product_image(request.files.get('image'))
            if new_image:
                if product.image:
                    delete_product_image(product.image)
                product.image = new_image
            db.session.commit()
            flash('محصول ویرایش شد', 'success')
            return redirect(url_for('admin_products'))
        return render_template('admin/product_form.html', product=product)

    @app.route('/admin/products/delete/<int:pid>', methods=['POST'])
    @admin_required
    def admin_product_delete(pid):
        product = db.session.get(Product, pid)
        if product is None:
            abort(404)
        used = OrderItem.query.filter_by(product_id=product.id).count()
        if used:
            flash(f'این محصول در {used} سفارش استفاده شده و قابل حذف نیست. موجودی آن را صفر کنید.', 'danger')
            return redirect(url_for('admin_products'))
        image = product.image
        db.session.delete(product)
        db.session.commit()
        if image:
            delete_product_image(image)
        flash('محصول حذف شد', 'success')
        return redirect(url_for('admin_products'))

    @app.route('/admin/products/<int:pid>/discounts', methods=['POST'])
    @admin_required
    def admin_discount_add(pid):
        product = db.session.get(Product, pid)
        if product is None:
            abort(404)
        min_qty = parse_int(request.form.get('min_quantity'))
        max_qty = parse_int(request.form.get('max_quantity'))
        percent = parse_int(request.form.get('percent'))
        if percent is None or not 0 < percent <= 90:
            flash('درصد تخفیف باید بین ۱ تا ۹۰ باشد', 'danger')
            return redirect(url_for('admin_product_edit', pid=pid))
        if min_qty is None or min_qty < 1:
            flash('حداقل تعداد باید عدد مثبت باشد', 'danger')
            return redirect(url_for('admin_product_edit', pid=pid))
        if max_qty is not None and max_qty < min_qty:
            flash('حداکثر تعداد نمی‌تواند کمتر از حداقل باشد', 'danger')
            return redirect(url_for('admin_product_edit', pid=pid))
        db.session.add(Discount(
            product_id=product.id,
            min_quantity=min_qty,
            max_quantity=max_qty,
            percent=percent,
        ))
        db.session.commit()
        flash('تخفیف برای این محصول ثبت شد', 'success')
        return redirect(url_for('admin_product_edit', pid=pid))

    @app.route('/admin/products/<int:pid>/discounts/<int:did>/delete', methods=['POST'])
    @admin_required
    def admin_discount_delete(pid, did):
        discount = db.session.get(Discount, did)
        if discount is None or discount.product_id != pid:
            abort(404)
        db.session.delete(discount)
        db.session.commit()
        flash('تخفیف حذف شد', 'success')
        return redirect(url_for('admin_product_edit', pid=pid))

    @app.route('/admin/orders')
    @admin_required
    def admin_orders():
        orders = Order.query.order_by(Order.created_at.desc()).all()
        return render_template('admin/orders.html', orders=orders)

    @app.route('/admin/orders/<int:oid>')
    @admin_required
    def admin_order_detail(oid):
        order = db.session.get(Order, oid)
        if order is None:
            abort(404)
        items = [{
            'name': item.product.name if item.product else f'محصول #{item.product_id}',
            'quantity': item.quantity,
            'price': item.price,
        } for item in order.items]
        return render_template('admin/order_detail.html', order=order, items=items)

    @app.route('/admin/orders/<int:oid>/status', methods=['POST'])
    @admin_required
    def admin_order_status(oid):
        order = db.session.get(Order, oid)
        if order is None:
            abort(404)
        status = request.form.get('status')
        if status not in VALID_STATUSES:
            flash('وضعیت نامعتبر است', 'danger')
            return redirect(url_for('admin_orders'))
        if status == 'cancelled' and order.status == 'paid':
            change_stock(order, 1)
        order.status = status
        db.session.commit()
        flash('وضعیت سفارش تغییر شد', 'success')
        return redirect(url_for('admin_orders'))

    @app.route('/admin/users')
    @admin_required
    def admin_users():
        users = User.query.order_by(User.created_at.desc()).all()
        return render_template('admin/users.html', users=users)

    @app.route('/admin/users/<int:uid>/delete', methods=['POST'])
    @admin_required
    def admin_user_delete(uid):
        user = db.session.get(User, uid)
        if user is None:
            abort(404)
        db.session.query(Order).filter(Order.user_id == user.id).update({Order.user_id: None})
        db.session.delete(user)
        db.session.commit()
        flash('کاربر حذف شد', 'success')
        return redirect(url_for('admin_users'))

    @app.route('/admin/about', methods=['GET', 'POST'])
    @admin_required
    def admin_about():
        content = AboutContent.get_content()
        if request.method == 'POST':
            AboutContent.set_content(request.form.get('content', ''))
            flash('متن درباره ما ذخیره شد', 'success')
            return redirect(url_for('admin_about'))
        return render_template('admin/about.html', content=content)

    @app.route('/admin/footer', methods=['GET', 'POST'])
    @admin_required
    def admin_footer():
        if request.method == 'POST':
            FooterContent.save_values(**{
                field: (request.form.get(field) or '').strip()
                for field in FooterContent.FIELDS
            })
            flash('اطلاعات فوتر ذخیره شد', 'success')
            return redirect(url_for('admin_footer'))
        return render_template('admin/footer.html', **FooterContent.get_values())

    @app.errorhandler(404)
    def not_found(error):
        return render_template('404.html'), 404

    @app.errorhandler(403)
    def forbidden(error):
        return render_template('403.html'), 403

    return app


if __name__ == '__main__':
    create_app().run(debug=True, host='0.0.0.0', port=5000)
