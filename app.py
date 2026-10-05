from datetime import datetime, timedelta

import os
import secrets

from flask import Flask, abort, flash, redirect, render_template, request, session, url_for
from sqlalchemy import func

from config import *
from extensions import db, csrf
from func import *
from models.models import Coupon, Discount, Order, OrderItem, Product, User

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

STOCK_HELD_STATUSES = {'paid', 'shipped', 'completed'}

IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}

USERNAME_TAKEN = 'این نام کاربری قبلاً ثبت شده است'

INVALID_NUMBER = 'قیمت و موجودی باید عدد معتبر و مثبت باشد'


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = SECRET_KEY
    app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
    app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'images')
    app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024
    app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=90)
    app.config['SESSION_REFRESH_EACH_REQUEST'] = True

    db.init_app(app)
    csrf.init_app(app)

    with app.app_context():
        db.create_all()

    def save_product_image(file_storage):
        if not file_storage or not file_storage.filename:
            return None
        suffix = os.path.splitext(file_storage.filename)[1].lower()
        if suffix not in IMAGE_EXTENSIONS:
            flash('نوع فایل عکس مجاز نیست', 'danger')
            return None
        folder = app.config['UPLOAD_FOLDER']
        os.makedirs(folder, exist_ok=True)
        filename = f'{secrets.token_hex(16)}{suffix}'
        file_storage.save(os.path.join(folder, filename))
        return filename

    def delete_product_image(filename):
        target = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        if os.path.isfile(target):
            os.remove(target)

    @app.context_processor
    def inject_footer():
        return {'footer': footer_values()}

    @app.context_processor
    def inject_globals():
        cart = session.get('cart') or []
        return {
            'status_labels': STATUS_LABELS,
            'status_icons': STATUS_ICONS,
            'active_coupon': active_coupon(),
            'cart_count': sum(int(i.get('quantity') or 0) for i in cart
                              if str(i.get('quantity') or '').lstrip('-').isdigit()),
        }

    app.template_filter('fa_num')(fa_num)
    app.template_filter('fa_digits')(fa_digits)
    app.template_filter('fa_date')(fa_date)

    @app.route('/')
    def home():
        keyword = (request.args.get('q') or '').strip()[:100]
        return render_template('home.html', products=search_products(keyword), q=keyword)

    @app.route('/api/username-available', methods=['GET'])
    def username_available():
        username = (request.args.get('username') or '').strip()[:80]
        try:
            ignore_id = int(request.args.get('ignore_id') or 0)
        except ValueError:
            ignore_id = 0
        available = False
        if is_valid_username(username):
            query = User.query.filter(func.lower(User.username) == username.lower())
            if ignore_id:
                query = query.filter(User.id != ignore_id)
            available = query.first() is None
        return data_html(available=available, checked=username)

    @app.route('/api/products', methods=['GET'])
    def products_api():
        keyword = (request.args.get('q') or '').strip()[:100]
        products = search_products(keyword)
        grid = render_template('_product_grid.html', products=products, q=keyword)
        return f'<div data-count="{len(products)}" data-html="{escape_html(grid)}"></div>'

    @app.route('/about')
    def about():
        return render_template('about.html', content=about_content())

    @app.route('/product/<int:pid>')
    def product_detail(pid):
        product = db.session.get(Product, pid)
        if product is None:
            abort(404)
        global_discounts = Discount.query.filter(Discount.product_id.is_(None)).all()
        return render_template('product_detail.html', product=product, global_discounts=global_discounts)

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
        quantity = parse_int(request.form.get('quantity'), 1, minimum=1, maximum=100000)
        product = db.session.get(Product, product_id)
        if not product:
            flash('محصول یافت نشد', 'danger')
            return redirect(url_for('home'))
        if quantity is INVALID:
            flash('تعداد وارد شده معتبر نیست', 'danger')
            return redirect(url_for('cart'))
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
        requested = parse_int(request.form.get('quantity'), 1, minimum=0, maximum=100000)
        if requested is INVALID:
            requested = 1
        removed = False
        if match is None and requested > 0:
            if stock > 0:
                items.append({'product_id': pid, 'quantity': min(requested, stock)})
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
            if product and current:
                unit_price, percent = unit_price_for(
                    product, current, active_coupon().percent if active_coupon() else 0)
            else:
                unit_price, percent = 0, 0
            return data_html(
                ok=True, removed=removed, total=total, quantity=current,
                count=sum(i['quantity'] for i in details),
                unit_price=unit_price,
                percent=percent,
            )
        return redirect(url_for('cart'))

    @app.route('/cart/remove/<int:pid>', methods=['POST'])
    def cart_remove(pid):
        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        session['cart'] = [i for i in get_cart() if str(i['product_id']) != str(pid)]
        session.modified = True
        if not session['cart']:
            clear_coupon()
        if is_ajax:
            details, total = get_cart_details()
            return data_html(ok=True, total=total, count=sum(i['quantity'] for i in details))
        return redirect(url_for('cart'))

    @app.route('/coupon/apply', methods=['POST'])
    def coupon_apply():
        coupon, blocked = set_coupon(request.form.get('code'))
        if blocked:
            flash(blocked, 'danger')
        else:
            flash(f'کد تخفیف {coupon.percent}٪ اعمال شد', 'success')
        return redirect(safe_redirect_target(
            request.form.get('next'), url_for('cart')))

    @app.route('/coupon/remove', methods=['POST'])
    def coupon_remove():
        clear_coupon()
        flash('کد تخفیف حذف شد', 'info')
        return redirect(safe_redirect_target(
            request.form.get('next'), url_for('cart')))

    def cancel_order(order, message):
        if order.status in STOCK_HELD_STATUSES:
            change_stock(order, 1)
        order.status = 'cancelled'
        release_coupon(order)
        db.session.commit()
        flash(message, 'danger')
        return redirect(url_for('home'))

    @app.route('/checkout', methods=['GET', 'POST'])
    def checkout():
        if 'user_id' not in session:
            return redirect(url_for('user_login'))
        if not get_cart():
            flash('سبد خرید خالی است', 'danger')
            return redirect(url_for('cart'))
        details, total = get_cart_details()
        if not details:
            clear_coupon()
            session.pop('cart', None)
            session.modified = True
            flash('سبد خرید شما خالی است', 'danger')
            return redirect(url_for('cart'))
        user = db.session.get(User, session['user_id'])
        if user is None:
            session.pop('user_id', None)
            return redirect(url_for('user_login'))
        coupon = active_coupon()
        if request.method != 'POST':
            return render_template('checkout.html', items=details, total=total, user=user)
        name = clean_text(request.form.get('name'), 200)
        phone = clean_text(request.form.get('phone'), 20)
        address = clean_text(request.form.get('address'), 2000)
        if not all([name, phone, address]):
            flash('لطفا تمام فیلدها را پر کنید', 'danger')
        elif name is None or phone is None or address is None:
            flash('اطلاعات وارد شده بیش از حد طولانی است', 'danger')
        elif len(name) < 3 or len(address) < 5:
            flash('نام و آدرس را کامل وارد کنید', 'danger')
        elif not is_valid_phone(phone):
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
                payment_ref=secrets.token_hex(16),
                coupon_code=coupon.code if coupon else None,
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
        if 'user_id' not in session:
            return redirect(url_for('user_login'))
        order = db.session.get(Order, oid)
        if order is None:
            abort(404)
        if order.user_id != session['user_id']:
            flash('شما مجوز پرداخت این سفارش را ندارید', 'danger')
            return redirect(url_for('profile'))
        if order.status == 'paid':
            return redirect(url_for('order_success', oid=order.id))
        if order.status == 'cancelled':
            flash('این سفارش لغو شده است', 'danger')
            return redirect(url_for('home'))
        if not order_stock_available(order):
            return cancel_order(order, 'موجودی برخی اقلام کافی نیست و سفارش لغو شد؛ دوباره خریداری کنید')
        return render_template('payment.html', order=order)

    @app.route('/payment/verify', methods=['POST'])
    def payment_verify():
        if 'user_id' not in session:
            return redirect(url_for('user_login'))
        reference = request.form.get('payment_ref')
        order = Order.query.filter_by(payment_ref=reference).first()
        if order is None:
            abort(404)
        if order.user_id != session['user_id']:
            flash('شما مجوز پرداخت این سفارش را ندارید', 'danger')
            return redirect(url_for('profile'))
        if order.status != 'pending':
            flash('این سفارش هنوز قابل پرداخت نیست', 'danger')
            return redirect(url_for('order_success', oid=order.id))
        if order.coupon_code and not order.coupon_consumed:
            spent = coupon_blocked(find_coupon(order.coupon_code))
            if spent:
                flash(spent, 'danger')
                return redirect(url_for('order_success', oid=order.id))
        if request.form.get('result') == 'fail':
            return cancel_order(order, 'پرداخت ناموفق بود و سفارش لغو شد')
        if not order_stock_available(order):
            return cancel_order(order, 'موجودی برخی اقلام کافی نیست و سفارش لغو شد')
        order.status = 'paid'
        consume_coupon(order)
        change_stock(order, -1)
        db.session.commit()
        clear_coupon()
        flash('پرداخت با موفقیت انجام شد', 'success')
        return redirect(url_for('order_success', oid=order.id))

    @app.route('/order/<int:oid>')
    def order_success(oid):
        if 'user_id' not in session:
            return redirect(url_for('user_login'))
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
    def order_pay(oid):
        if 'user_id' not in session:
            return redirect(url_for('user_login'))
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
        username = clean_text(request.form.get('username'), 80)
        password = request.form.get('password', '')
        full_name = clean_text(request.form.get('full_name'), 200)
        phone = clean_text(request.form.get('phone'), 20)
        address = clean_text(request.form.get('address'), 2000)
        if not username or not password:
            flash('نام کاربری و رمز عبور الزامی است', 'danger')
        elif len(password) > 256:
            flash('رمز عبور خیلی بلند است', 'danger')
        elif len(password) < 6:
            flash('رمز عبور باید حداقل ۶ حرف باشد', 'danger')
        elif username is None or full_name is None or phone is None or address is None:
            flash('اطلاعات وارد شده بیش از حد طولانی است', 'danger')
        elif not is_valid_username(username):
            flash('نام کاربری باید بین ۳ تا ۸۰ حرف و بدون فاصله باشد', 'danger')
        elif not is_valid_phone(phone):
            flash('شماره موبایل معتبر نیست', 'danger')
        elif password != request.form.get('password_confirm', ''):
            flash('رمز عبور و تکرار آن مطابقت ندارد', 'danger')
        elif User.query.filter(func.lower(User.username) == username.lower()).first():
            flash(USERNAME_TAKEN, 'danger')
        else:
            user = User(username=username, full_name=full_name, phone=phone, address=address)
            user.password_hash = hash_password(password)
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
            if user and verify_password(user.password_hash, request.form.get('password', '')):
                session['user_id'] = user.id
                session.permanent = True
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
    def profile():
        if 'user_id' not in session:
            return redirect(url_for('user_login'))
        user = db.session.get(User, session['user_id'])
        if user is None:
            session.pop('user_id', None)
            return redirect(url_for('user_login'))
        orders = user.orders
        context = {
            'user': user,
            'active_orders': len([o for o in orders if o.status != 'cancelled']),
            'pending_orders': len([o for o in orders if o.status == 'pending']),
        }
        if request.method != 'POST':
            return render_template('profile.html', **context)
        new_username = clean_text(request.form.get('username'), 80)
        full_name = clean_text(request.form.get('full_name'), 200)
        phone = clean_text(request.form.get('phone'), 20)
        address = clean_text(request.form.get('address'), 2000)
        if not new_username:
            flash('نام کاربری نمی‌تواند خالی باشد', 'danger')
            return render_template('profile.html', **context)
        if not is_valid_username(new_username):
            flash('نام کاربری باید بین ۳ تا ۸۰ حرف و بدون فاصله باشد', 'danger')
            return render_template('profile.html', **context)
        if not is_valid_phone(phone):
            flash('شماره موبایل معتبر نیست', 'danger')
            return render_template('profile.html', **context)
        if full_name is None or address is None:
            flash('اطلاعات وارد شده بیش از حد طولانی است', 'danger')
            return render_template('profile.html', **context)
        taken = User.query.filter(
            func.lower(User.username) == new_username.lower(),
            User.id != user.id,
        ).first()
        if taken:
            flash('این نام کاربری توسط کاربر دیگری در استفاده است', 'danger')
            return render_template('profile.html', **context)
        user.username = new_username
        user.full_name = full_name
        user.phone = phone
        user.address = address
        db.session.commit()
        flash('اطلاعات حساب به‌روز شد', 'success')
        return redirect(url_for('profile'))

    @app.route('/admin/login', methods=['GET', 'POST'])
    def admin_login():
        if request.method == 'POST':
            username = clean_text(request.form.get('username'), 80) or ''
            password = request.form.get('password', '') or ''
            if len(password) <= 256 and username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
                session['admin_id'] = 1
                session.permanent = True
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
    def admin_dashboard():
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
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
    def admin_products():
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        return render_template('admin/products.html', products=Product.query.all())

    @app.route('/admin/products/add', methods=['GET', 'POST'])
    def admin_product_add():
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        if request.method == 'POST':
            name = clean_text(request.form.get('name'), 200)
            price = parse_number(request.form.get('price'))
            stock = parse_int(request.form.get('stock'), 0, minimum=0, maximum=1000000)
            if not name:
                flash('نام و قیمت محصول الزامی است', 'danger')
                return render_template('admin/product_form.html')
            if price is None or price < 0 or price > 99999999 or stock is INVALID:
                flash(INVALID_NUMBER, 'danger')
                return render_template('admin/product_form.html')
            product = Product(
                code=optional_text(request.form.get('code'), 100),
                name=name,
                description=optional_text(request.form.get('description'), 5000),
                price=price,
                stock=stock,
                category=optional_text(request.form.get('category'), 100),
                image=save_product_image(request.files.get('image')),
            )
            db.session.add(product)
            db.session.commit()
            flash('محصول با موفقیت اضافه شد', 'success')
            return redirect(url_for('admin_products'))
        return render_template('admin/product_form.html')

    @app.route('/admin/products/edit/<int:pid>', methods=['GET', 'POST'])
    def admin_product_edit(pid):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        product = db.session.get(Product, pid)
        if product is None:
            abort(404)
        if request.method == 'POST':
            name = clean_text(request.form.get('name'), 200)
            price = parse_number(request.form.get('price'))
            stock = parse_int(request.form.get('stock'), 0, minimum=0, maximum=1000000)
            raw_code = request.form.get('code', '')
            raw_description = request.form.get('description', '')
            raw_category = request.form.get('category', '')
            too_long = (len(raw_code) > 100 or len(raw_description) > 5000
                        or len(raw_category) > 100)
            code = optional_text(raw_code, 100)
            description = optional_text(raw_description, 5000)
            category = optional_text(raw_category, 100)
            if not name:
                flash('نام محصول الزامی است', 'danger')
                return render_template('admin/product_form.html', product=product,
                                       global_discounts=Discount.query.filter(Discount.product_id.is_(None)).all())
            if price is None or price < 0 or price > 99999999 or stock is INVALID \
                    or too_long:
                flash(INVALID_NUMBER, 'danger')
                return render_template('admin/product_form.html', product=product,
                                       global_discounts=Discount.query.filter(Discount.product_id.is_(None)).all())
            product.code = code
            product.name = name
            product.description = description
            product.price = price
            product.stock = stock
            product.category = category
            new_image = save_product_image(request.files.get('image'))
            if new_image:
                if product.image:
                    delete_product_image(product.image)
                product.image = new_image
            db.session.commit()
            flash('محصول ویرایش شد', 'success')
            return redirect(url_for('admin_products'))
        return render_template('admin/product_form.html', product=product,
                               global_discounts=Discount.query.filter(Discount.product_id.is_(None)).all())

    @app.route('/admin/products/delete/<int:pid>', methods=['POST'])
    def admin_product_delete(pid):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        product = db.session.get(Product, pid)
        if product is None:
            abort(404)
        used = OrderItem.query.filter_by(product_id=product.id).count()
        if used:
            flash(f'این محصول در {used} سفارش استفاده شده و قابل حذف نیست. موجودی آن را صفر کنید.', 'danger')
            return redirect(url_for('admin_products'))
        image = product.image
        owned = Discount.query.filter_by(product_id=product.id).delete()
        db.session.delete(product)
        db.session.commit()
        if image:
            delete_product_image(image)
        if owned:
            flash(f'محصول و {owned} تخفیف آن حذف شد', 'success')
        else:
            flash('محصول حذف شد', 'success')
        return redirect(url_for('admin_products'))

    @app.route('/admin/products/<int:pid>/discounts', methods=['POST'])
    def admin_discount_add(pid):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        product = db.session.get(Product, pid)
        if product is None:
            abort(404)
        min_qty = parse_int(request.form.get('min_quantity'), minimum=1, maximum=100000)
        max_qty = parse_optional_int(request.form.get('max_quantity'), minimum=1, maximum=100000)
        percent = parse_int(request.form.get('percent'), minimum=1, maximum=90)
        if percent is INVALID:
            flash('درصد تخفیف باید بین ۱ تا ۹۰ باشد', 'danger')
            return redirect(url_for('admin_product_edit', pid=pid))
        if min_qty is INVALID:
            flash('حداقل تعداد باید عدد مثبت باشد', 'danger')
            return redirect(url_for('admin_product_edit', pid=pid))
        if max_qty is INVALID:
            flash('حداکثر تعداد باید عدد مثبت باشد', 'danger')
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
    def admin_discount_delete(pid, did):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        discount = db.session.get(Discount, did)
        if discount is None or discount.product_id != pid:
            abort(404)
        db.session.delete(discount)
        db.session.commit()
        flash('تخفیف حذف شد', 'success')
        return redirect(url_for('admin_product_edit', pid=pid))

    @app.route('/admin/discounts')
    def admin_discounts():
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        global_discounts = Discount.query.filter(Discount.product_id.is_(None)).all()
        return render_template('admin/discounts.html', global_discounts=global_discounts)

    @app.route('/admin/discounts/add', methods=['POST'])
    def admin_global_discount_add():
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        min_qty = parse_int(request.form.get('min_quantity'), minimum=1, maximum=100000)
        max_qty = parse_optional_int(request.form.get('max_quantity'), minimum=1, maximum=100000)
        max_items = parse_optional_int(request.form.get('max_items'), minimum=1, maximum=100000)
        title = optional_text(request.form.get('title'), 200)
        percent = parse_int(request.form.get('percent'), minimum=1, maximum=90)
        if percent is INVALID:
            flash('درصد تخفیف باید بین ۱ تا ۹۰ باشد', 'danger')
            return redirect(url_for('admin_discounts'))
        if min_qty is INVALID:
            flash('حداقل تعداد باید عدد مثبت باشد', 'danger')
            return redirect(url_for('admin_discounts'))
        if max_qty is INVALID:
            flash('حداکثر تعداد باید عدد مثبت باشد', 'danger')
            return redirect(url_for('admin_discounts'))
        if max_items is INVALID:
            flash('حداکثر تعداد کالا باید عدد مثبت باشد', 'danger')
            return redirect(url_for('admin_discounts'))
        if max_qty is not None and max_qty < min_qty:
            flash('حداکثر تعداد نمی‌تواند کمتر از حداقل باشد', 'danger')
            return redirect(url_for('admin_discounts'))
        existing = Discount.query.filter(Discount.product_id.is_(None)).all()
        if existing and request.form.get('replace') != 'yes':
            old = existing[0]
            label = old.title or f'تخفیف {old.percent}٪'
            flash(
                f'یک تخفیف سراسری فعال است ({label}). اگر تایید می‌کنید، '
                f'آن حذف و این تخفیف جایگزین می‌شود.', 'warning')
            return redirect(url_for('admin_discounts'))
        for old in existing:
            db.session.delete(old)
        db.session.add(Discount(
            product_id=None,
            title=title,
            min_quantity=min_qty,
            max_quantity=max_qty,
            max_items=max_items,
            percent=percent,
        ))
        db.session.commit()
        if existing:
            flash('تخفیف سراسری قبلی حذف و تخفیف جدید اعمال شد', 'success')
        else:
            flash('تخفیف سراسری ثبت شد و روی همه محصولات اعمال می‌شود', 'success')
        return redirect(url_for('admin_discounts'))

    @app.route('/admin/discounts/<int:did>/delete', methods=['POST'])
    def admin_global_discount_delete(did):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        discount = db.session.get(Discount, did)
        if discount is None or discount.product_id is not None:
            abort(404)
        db.session.delete(discount)
        db.session.commit()
        flash('تخفیف سراسری حذف شد', 'success')
        return redirect(url_for('admin_discounts'))

    @app.route('/admin/coupons')
    def admin_coupons():
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        return render_template('admin/coupons.html',
                               coupons=Coupon.query.order_by(Coupon.id.desc()).all())

    @app.route('/admin/coupons/add', methods=['POST'])
    def admin_coupon_add():
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        code = normalize_coupon(request.form.get('code'))
        if len(code) > 50:
            flash('کد تخفیف نباید بیشتر از ۵۰ حرف باشد', 'danger')
            return redirect(url_for('admin_coupons'))
        percent = parse_int(request.form.get('percent'), minimum=1, maximum=90)
        expires = parse_jalali_date(request.form.get('expires_at'))
        max_uses = parse_int(request.form.get('max_uses'), minimum=1, maximum=1000000)
        item_limit = parse_optional_int(request.form.get('item_limit'), minimum=1, maximum=100000)
        title = optional_text(request.form.get('title'), 200)
        if not code:
            flash('کد تخفیف الزامی است', 'danger')
            return redirect(url_for('admin_coupons'))
        if Coupon.query.filter_by(code=code).first():
            flash('این کد تخفیف قبلاً ثبت شده است', 'danger')
            return redirect(url_for('admin_coupons'))
        if percent is INVALID:
            flash('درصد تخفیف باید بین ۱ تا ۹۰ باشد', 'danger')
            return redirect(url_for('admin_coupons'))
        raw_expires = (request.form.get('expires_at') or '').strip()
        if raw_expires and expires is None:
            flash('تاریخ انقضا معتبر نیست — قالب سال/ماه/روز شمسی، مثل ۱۴۰۵/۰۷/۱۲', 'danger')
            return redirect(url_for('admin_coupons'))
        if expires is not None and expires.date() < datetime.now().date():
            flash('تاریخ انقضا نمی‌تواند در گذشته باشد', 'danger')
            return redirect(url_for('admin_coupons'))
        if max_uses is INVALID:
            flash('سقف استفاده باید عدد مثبت باشد', 'danger')
            return redirect(url_for('admin_coupons'))
        if item_limit is INVALID:
            flash('تعداد کالا باید عدد مثبت باشد', 'danger')
            return redirect(url_for('admin_coupons'))
        db.session.add(Coupon(code=code, title=title, percent=percent,
                              item_limit=item_limit, expires_at=expires,
                              max_uses=max_uses))
        db.session.commit()
        scope = f' روی {item_limit} کالای اول سبد' if item_limit else ''
        flash(f'کد تخفیف {code} با {percent}٪ ثبت شد{scope}', 'success')
        return redirect(url_for('admin_coupons'))

    @app.route('/admin/coupons/<int:cid>/delete', methods=['POST'])
    def admin_coupon_delete(cid):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        coupon = db.session.get(Coupon, cid)
        if coupon is None:
            abort(404)
        db.session.delete(coupon)
        db.session.commit()
        flash('کد تخفیف حذف شد', 'success')
        return redirect(url_for('admin_coupons'))

    @app.route('/admin/orders')
    def admin_orders():
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        orders = Order.query.order_by(Order.created_at.desc()).all()
        return render_template('admin/orders.html', orders=orders)

    @app.route('/admin/orders/<int:oid>')
    def admin_order_detail(oid):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
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
    def admin_order_status(oid):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        order = db.session.get(Order, oid)
        if order is None:
            abort(404)
        status = request.form.get('status')
        if status not in VALID_STATUSES:
            flash('وضعیت نامعتبر است', 'danger')
            return redirect(url_for('admin_orders'))
        was_holding = order.status in STOCK_HELD_STATUSES
        now_holding = status in STOCK_HELD_STATUSES
        if was_holding and not now_holding:
            change_stock(order, 1)
        elif now_holding and not was_holding:
            if not order_stock_available(order):
                flash('موجودی کافی نیست؛ وضعیت سفارش تغییر نکرد', 'danger')
                return redirect(url_for('admin_orders'))
            change_stock(order, -1)
        order.status = status
        if status == 'cancelled':
            release_coupon(order)
        db.session.commit()
        flash('وضعیت سفارش تغییر شد', 'success')
        return redirect(url_for('admin_orders'))

    @app.route('/admin/users')
    def admin_users():
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        users = User.query.order_by(User.created_at.desc()).all()
        return render_template('admin/users.html', users=users)

    @app.route('/admin/users/<int:uid>/delete', methods=['POST'])
    def admin_user_delete(uid):
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        user = db.session.get(User, uid)
        if user is None:
            abort(404)
        db.session.query(Order).filter(Order.user_id == user.id).update({Order.user_id: None})
        db.session.delete(user)
        db.session.commit()
        flash('کاربر حذف شد', 'success')
        return redirect(url_for('admin_users'))

    @app.route('/admin/about', methods=['GET', 'POST'])
    def admin_about():
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        content = about_content()
        if request.method == 'POST':
            text = clean_text(request.form.get('content'), 20000)
            if text is None:
                flash('متن درباره ما بیش از حد طولانی است', 'danger')
                return redirect(url_for('admin_about'))
            save_about_content(text)
            flash('متن درباره ما ذخیره شد', 'success')
            return redirect(url_for('admin_about'))
        return render_template('admin/about.html', content=content)

    @app.route('/admin/footer', methods=['GET', 'POST'])
    def admin_footer():
        if 'admin_id' not in session:
            return redirect(url_for('admin_login'))
        if request.method == 'POST':
            values = {
                field: optional_text(request.form.get(field), 200)
                for field in FOOTER_FIELDS
            }
            if any(len(request.form.get(field, '')) > 200
                   for field in FOOTER_FIELDS):
                flash('متن وارد شده بیش از حد طولانی است', 'danger')
                return redirect(url_for('admin_footer'))
            save_footer_values(**values)
            flash('اطلاعات فوتر ذخیره شد', 'success')
            return redirect(url_for('admin_footer'))
        return render_template('admin/footer.html', **footer_values())

    @app.errorhandler(404)
    def not_found(error):
        return render_template('404.html'), 404

    @app.errorhandler(403)
    def forbidden(error):
        return render_template('403.html'), 403

    @app.errorhandler(413)
    def too_large(error):
        flash('حجم فایل ارسالی بیش از حد مجاز است', 'danger')
        return redirect(url_for('admin_products'))

    return app


if __name__ == '__main__':
    create_app().run(debug=True, host='0.0.0.0', port=5000)
