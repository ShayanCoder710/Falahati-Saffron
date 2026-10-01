# -*- coding: utf-8 -*-
"""
سایت فروشگاهی زعفران فلاحتی
بک‌اند: Flask + Flask-SQLAlchemy
پایگاه داده: MariaDB (localhost:3306)
"""
from flask import Flask, render_template, request, redirect, url_for, flash, session
from config import *
from extensions import db, csrf
from models.models import Admin, Product, Order, OrderItem, AboutContent, User
import uuid
import os


def create_app():
    app = Flask(__name__)
    app.config['SECRET_KEY'] = SECRET_KEY
    app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = SQLALCHEMY_TRACK_MODIFICATIONS
    app.config['UPLOAD_FOLDER'] = os.path.join(app.root_path, 'static', 'images')
    db.init_app(app)
    csrf.init_app(app)

    def save_product_image(file_storage):
        """ذخیره عکس محصول و برگشتن نام فایل آن در static/images"""
        if not file_storage or not file_storage.filename:
            return None
        ext = os.path.splitext(file_storage.filename)[1].lower()
        if ext not in ('.png', '.jpg', '.jpeg', '.webp', '.gif'):
            flash('نوع فایل عکس مجاز نیست', 'danger')
            return None
        os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
        filename = f"{uuid.uuid4().hex}{ext}"
        file_storage.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        return filename

    with app.app_context():
        db.create_all()
        if not Admin.query.filter_by(username='admin').first():
            admin = Admin(username='admin')
            admin.set_password(ADMIN_PASSWORD)
            db.session.add(admin)
            db.session.commit()

    def admin_required(f):
        from functools import wraps
        @wraps(f)
        def decorated(*a, **kw):
            if 'admin_id' not in session:
                return redirect(url_for('admin_login'))
            return f(*a, **kw)
        return decorated

    def login_required(f):
        from functools import wraps
        @wraps(f)
        def decorated(*a, **kw):
            if 'user_id' not in session:
                return redirect(url_for('user_login'))
            return f(*a, **kw)
        return decorated

    # ─── صفحات عمومی ────────────────────────────────────────────────────────────
    @app.route('/')
    def home():
        return render_template('home.html', products=Product.query.all())

    @app.route('/about')
    def about():
        return render_template('about.html', content=AboutContent.get_content())

    @app.route('/product/<int:pid>')
    def product_detail(pid):
        return render_template('product_detail.html', product=Product.query.get_or_404(pid))

    # ─── سبد خرید ──────────────────────────────────────────────────────────────
    @app.route('/cart', methods=['GET', 'POST'])
    def cart():
        if 'cart' not in session:
            session['cart'] = []
            session.modified = True
        if request.method == 'POST':
            pid = request.form.get('product_id')
            qty = max(1, int(request.form.get('quantity', 1) or 1))
            p = Product.query.get(pid)
            if not p:
                flash('محصول یافت نشد', 'danger')
                return redirect(url_for('home'))
            items = session.get('cart', [])
            current = next((i['quantity'] for i in items if str(i['product_id']) == str(pid)), 0)
            new_qty = current + qty
            if new_qty > p.stock:
                flash(f"تنها {p.stock} عدد از این محصول موجود است", 'danger')
                qty = max(0, p.stock - current)
                if qty == 0:
                    return redirect(url_for('cart'))
            if current:
                for i in items:
                    if str(i['product_id']) == str(pid):
                        i['quantity'] += qty
                        break
            else:
                items.append({'product_id': pid, 'quantity': qty})
            session['cart'] = items
            session.modified = True
            flash('محصول به سبد اضافه شد', 'success')
            return redirect(url_for('cart'))
        items, total = [], 0
        for it in session.get('cart', []):
            p = Product.query.get(it['product_id'])
            if p:
                qty = min(int(it['quantity']), p.stock)
                items.append({'product': p, 'quantity': qty, 'max_qty': p.stock})
                total += float(p.price) * qty
        return render_template('cart.html', items=items, total=total)

    @app.route('/cart/update/<int:iid>', methods=['POST'])
    def cart_update(iid):
        items = session.get('cart', [])
        if 0 <= iid < len(items):
            qty = int(request.form.get('quantity', 1) or 1)
            p = Product.query.get(items[iid]['product_id'])
            qty = max(1, min(qty, p.stock if p else qty))
            items[iid]['quantity'] = qty
            session['cart'] = items
            session.modified = True
        return redirect(url_for('cart'))

    @app.route('/cart/remove/<int:iid>', methods=['POST'])
    def cart_remove(iid):
        items = session.get('cart', [])
        if 0 <= iid < len(items):
            items.pop(iid)
            session['cart'] = items
            session.modified = True
        return redirect(url_for('cart'))

    # ─── پرداخت ────────────────────────────────────────────────────────────────
    def create_zarinpal_order(total, order):
        """اتصال به درگاه سندباکس زرین‌پال؛ بدون کلید، حالت mock فعال می‌شود."""
        import requests as _req
        if ZARINPAL_SANDBOX_KEY:
            try:
                resp = _req.post(
                    f"{ZARINPAL_SANDBOX_API}/request",
                    headers={'Merchant-Id': ZARINPAL_MERCHANT_ID,
                             'Zarinpal-Sandbox-Key': ZARINPAL_SANDBOX_KEY,
                             'Content-Type': 'application/json'},
                    json={'amount': int(total),
                          'description': f"Saffron-Felahati order #{order.id}",
                          'callbackURL': url_for('payment_verify', _external=True)},
                    timeout=10,
                )
                data = resp.json()
                if data.get('status') in ('success', 'existed'):
                    return data.get('authority'), data.get('trackingNumber') or order.payment_ref, True
                flash(f"خطا در اتصال به درگاه: {data.get('errorMessage', data.get('message', resp.text))}", 'danger')
                return None, order.payment_ref, False
            except Exception as e:
                flash(f"اتصال به درگاه برقرار نشد: {e}", 'danger')
                return None, order.payment_ref, False
        # حالت mock (بدون کلید سندباکس)
        return 'mock-' + order.payment_ref, order.payment_ref, True

    @app.route('/checkout', methods=['GET', 'POST'])
    @login_required
    def checkout():
        items = session.get('cart', [])
        if not items:
            flash('سبد خرید خالی است', 'danger')
            return redirect(url_for('cart'))
        detail, total, stock_ok = [], 0, True
        for it in items:
            p = Product.query.get(it['product_id'])
            if p:
                qty = min(int(it['quantity']), p.stock)
                if qty < int(it['quantity']):
                    stock_ok = False
                detail.append({'product': p, 'quantity': qty})
                total += float(p.price) * qty
        user = User.query.get(session['user_id'])
        if request.method == 'POST':
            name, phone, addr = request.form.get('name'), request.form.get('phone'), request.form.get('address')
            if not all([name, phone, addr]):
                flash('لطفا تمام فیلدها را پر کنید', 'danger')
                return render_template('checkout.html', items=detail, total=total)
            insufficient = [it for it in detail if it['product'].stock < it['quantity']]
            if insufficient:
                flash('موجودی برخی محصولات کافی نیست، تعداد را در سبد خرید کاهش دهید', 'danger')
                return redirect(url_for('cart'))
            order = Order(
                user_id=session['user_id'],
                customer_name=name, customer_phone=phone,
                customer_address=addr, total_price=total
            )
            db.session.add(order)
            db.session.flush()
            for it in detail:
                db.session.add(OrderItem(
                    order_id=order.id, product_id=it['product'].id,
                    quantity=it['quantity'], price=it['product'].price
                ))
                it['product'].stock -= it['quantity']
            order.payment_ref = str(uuid.uuid4())
            order.status = 'pending'
            db.session.commit()
            session.pop('cart', None)
            return redirect(url_for('payment_gateway', oid=order.id))
        return render_template('checkout.html', items=detail, total=total, user=user, stock_ok=stock_ok)

    @app.route('/payment/<int:oid>')
    def payment_gateway(oid):
        order = Order.query.get_or_404(oid)
        if order.status == 'paid':
            return redirect(url_for('order_success', oid=order.id))
        authority, tracking, ok = create_zarinpal_order(order.total_price, order)
        if not ok:
            return redirect(url_for('checkout'))
        return render_template('payment.html', order=order,
                               payment_url=f"https://sandbox.zarinpal.com/pg/startpay/{tracking}",
                               mock=not ZARINPAL_SANDBOX_KEY)

    @app.route('/payment/verify', methods=['POST'])
    @login_required
    def payment_verify():
        ref = request.form.get('payment_ref')
        order = Order.query.filter_by(payment_ref=ref).first_or_404()
        if ZARINPAL_SANDBOX_KEY:
            import requests as _req
            try:
                resp = _req.post(
                    f"{ZARINPAL_SANDBOX_API}/verify/{ref}",
                    headers={'Merchant-Id': ZARINPAL_MERCHANT_ID,
                             'Zarinpal-Sandbox-Key': ZARINPAL_SANDBOX_KEY,
                             'Content-Type': 'application/json'},
                    timeout=10,
                )
                data = resp.json()
                if data.get('status') != 'success':
                    order.status = 'cancelled'
                    db.session.commit()
                    flash(f"پرداخت موفق نبود: {data.get('errorMessage', 'خطای نامشخص')}", 'danger')
                    return redirect(url_for('home'))
            except Exception:
                flash("اتصال به درگاه برقرار نشد؛ مبلغ را به‌صورت دستی بررسی کنید", 'danger')
                return redirect(url_for('order_success', oid=order.id))
        order.status = 'paid'
        db.session.commit()
        flash('پرداخت با موفقیت انجام شد', 'success')
        return redirect(url_for('order_success', oid=order.id))

    @app.route('/order/<int:oid>')
    def order_success(oid):
        order = Order.query.get_or_404(oid)
        if order.status != 'paid':
            flash('این سفارش پرداخت نشده است', 'danger')
            return redirect(url_for('home'))
        return render_template('order_success.html', order=order)

    # ─── احراز هویت کاربران ────────────────────────────────────────────────────
    @app.route('/register', methods=['GET', 'POST'])
    def user_register():
        if request.method == 'POST':
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            password_confirm = request.form.get('password_confirm', '')
            full_name = request.form.get('full_name', '').strip()
            phone = request.form.get('phone', '').strip()
            address = request.form.get('address', '').strip()

            if not username or not password:
                flash('نام کاربری و رمز عبور الزامی است', 'danger')
                return render_template('user_register.html')
            if password != password_confirm:
                flash('رمز عبور و تکرار آن مطابقت ندارد', 'danger')
                return render_template('user_register.html')
            if User.query.filter_by(username=username).first():
                flash('این نام کاربری قبلاً ثبت شده است', 'danger')
                return render_template('user_register.html')

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
            username = request.form.get('username', '').strip()
            password = request.form.get('password', '')
            user = User.query.filter_by(username=username).first()
            if user and user.check_password(password):
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

    @app.route('/profile')
    @login_required
    def profile():
        user = User.query.get(session['user_id'])
        orders = Order.query.filter_by(user_id=user.id).order_by(Order.created_at.desc()).all()
        return render_template('profile.html', user=user, orders=orders)

    # ─── پنل ادمین ─────────────────────────────────────────────────────────────
    @app.route('/admin/login', methods=['GET', 'POST'])
    def admin_login():
        if request.method == 'POST':
            u, p = request.form.get('username'), request.form.get('password')
            admin = Admin.query.filter_by(username=u).first()
            if admin and admin.check_password(p):
                session['admin_id'] = admin.id
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
        return render_template('admin/dashboard.html',
                             products_count=Product.query.count(),
                             orders_count=Order.query.count(),
                             users_count=User.query.count(),
                             pending_orders=Order.query.filter_by(status='pending').count(),
                             paid_orders=Order.query.filter_by(status='paid').count(),
                             recent_orders=Order.query.order_by(Order.created_at.desc()).limit(10).all(),
                             low_stock_products=Product.query.filter(Product.stock < 5).all())

    @app.route('/admin/products')
    @admin_required
    def admin_products():
        return render_template('admin/products.html', products=Product.query.all())

    @app.route('/admin/products/add', methods=['GET', 'POST'])
    @admin_required
    def admin_product_add():
        if request.method == 'POST':
            name, price = request.form.get('name'), request.form.get('price')
            if not name or not price:
                flash('نام و قیمت محصول الزامی است', 'danger')
                return render_template('admin/product_form.html')
            image = save_product_image(request.files.get('image'))
            p = Product(code=request.form.get('code', ''),
                        name=name, description=request.form.get('description', ''),
                        price=float(price), stock=int(request.form.get('stock', 0) or 0),
                        category=request.form.get('category', ''), image=image)
            db.session.add(p)
            db.session.commit()
            flash('محصول با موفقیت اضافه شد', 'success')
            return redirect(url_for('admin_products'))
        return render_template('admin/product_form.html')

    @app.route('/admin/products/edit/<int:pid>', methods=['GET', 'POST'])
    @admin_required
    def admin_product_edit(pid):
        product = Product.query.get_or_404(pid)
        if request.method == 'POST':
            product.code = request.form.get('code', '')
            product.name = request.form.get('name')
            product.description = request.form.get('description', '')
            product.price = float(request.form.get('price'))
            product.stock = int(request.form.get('stock', 0) or 0)
            product.category = request.form.get('category', '')
            new_image = save_product_image(request.files.get('image'))
            if new_image:
                if product.image:
                    old = os.path.join(app.config['UPLOAD_FOLDER'], product.image)
                    if os.path.exists(old):
                        os.remove(old)
                product.image = new_image
            db.session.commit()
            flash('محصول ویرایش شد', 'success')
            return redirect(url_for('admin_products'))
        return render_template('admin/product_form.html', product=product)

    @app.route('/admin/products/delete/<int:pid>', methods=['POST'])
    @admin_required
    def admin_product_delete(pid):
        p = Product.query.get_or_404(pid)
        db.session.delete(p)
        db.session.commit()
        flash('محصول حذف شد', 'success')
        return redirect(url_for('admin_products'))

    @app.route('/admin/orders')
    @admin_required
    def admin_orders():
        return render_template('admin/orders.html', orders=Order.query.order_by(Order.created_at.desc()).all())

    @app.route('/admin/orders/<int:oid>/status', methods=['POST'])
    @admin_required
    def admin_order_status(oid):
        o = Order.query.get_or_404(oid)
        o.status = request.form.get('status')
        db.session.commit()
        flash('وضعیت سفارش تغییر کرد', 'success')
        return redirect(url_for('admin_orders'))

    @app.route('/admin/users')
    @admin_required
    def admin_users():
        users = User.query.order_by(User.created_at.desc()).all()
        return render_template('admin/users.html', users=users)

    @app.route('/admin/about', methods=['GET', 'POST'])
    @admin_required
    def admin_about():
        content = AboutContent.get_content()
        if request.method == 'POST':
            AboutContent.set_content(request.form.get('content', ''))
            flash('متن درباره ما ذخیره شد', 'success')
            return redirect(url_for('admin_about'))
        return render_template('admin/about.html', content=content)

    # ─── خطاها ──────────────────────────────────────────────────────────────────
    @app.errorhandler(404)
    def not_found(e):
        return render_template('404.html'), 404

    @app.errorhandler(403)
    def forbidden(e):
        return render_template('403.html'), 403

    return app


if __name__ == '__main__':
    application = create_app()
    application.run(debug=True, host='0.0.0.0', port=5000)
