"""End-to-end test: full purchase flow with Zarinpal mock + stock cap."""
import re, os, sys
sys.path.insert(0, os.path.dirname(__file__))
from app import create_app
from models.models import Product, Order, User, OrderItem
from extensions import db

app = create_app()
PASSED, FAILED = [], []

def check(name, cond):
    (PASSED if cond else FAILED).append(name)
    print(('  OK  ' if cond else 'FAIL ') + name)

# ── admin: create product with stock 5 ─────────────────────────────────────
with app.test_client() as ac:
    r = ac.get('/admin/login')
    atok = re.search(r'name="csrf_token" value="([^"]+)"', r.data.decode()).group(1)
    ac.post('/admin/login', data={'username':'admin','password':'Safferon@2024','csrf_token':atok})
    r = ac.get('/admin/products/add')
    atok = re.search(r'name="csrf_token" value="([^"]+)"', r.data.decode()).group(1)
    ac.post('/admin/products/add', data={'code':'E2E-01','name':'زعفران تست','price':'100000','stock':'5','csrf_token':atok}, follow_redirects=True)

with app.app_context():
    p = Product.query.filter_by(code='E2E-01').first()
    check('product created with stock=5', p is not None and p.stock == 5)

# ── user: register ────────────────────────────────────────────────────────
with app.test_client() as c:
    r = c.get('/register')
    tok = re.search(r'name="csrf_token" value="([^"]+)"', r.data.decode()).group(1)
    c.post('/register', data={'username':'buyer_e2e','password':'pw12345',
        'password_confirm':'pw12345','full_name':'خریدار تست','phone':'09120001111',
        'address':'تهران','csrf_token':tok}, follow_redirects=True)

    # add 3, then add 4 more → cap at 5
    c.post('/cart', data={'product_id':str(p.id),'quantity':'3','csrf_token':tok}, follow_redirects=True)
    r = c.post('/cart', data={'product_id':str(p.id),'quantity':'4','csrf_token':tok}, follow_redirects=True)
    check('cart caps at stock (5 not 7)', 'value="5"' in r.data.decode() or 'حداکثر 5' in r.data.decode())

    # update qty to 2
    c.post('/cart/update/0', data={'quantity':'2','csrf_token':tok}, follow_redirects=True)
    r = c.get('/cart')
    check('qty update to 2 works', 'value="2"' in r.data.decode())

    # checkout
    r = c.get('/checkout')
    c.post('/checkout', data={'name':'خریدار تست','phone':'09120001111','address':'تهران','csrf_token':tok}, follow_redirects=True)

    with app.app_context():
        o = Order.query.order_by(Order.id.desc()).first()
        check('order created', o is not None and o.total_price > 0)
        check('stock reduced to 3', Product.query.get(p.id).stock == 3)

    # payment page
    r = c.get(f'/payment/{o.id}')
    check('payment page renders', r.status_code == 200)
    check('mock mode active', 'mock' in r.data.decode().lower() or 'سندباکس' in r.data.decode())

    # verify payment
    m = re.search(r'name="csrf_token" value="([^"]+)"', r.data.decode())
    vt = m.group(1) if m else tok
    r = c.post('/payment/verify', data={'payment_ref':o.payment_ref,'csrf_token':vt}, follow_redirects=True)
    check('payment verified → order_success', 'سفارش شما ثبت شد' in r.data.decode())

    with app.app_context():
        check('order status=paid', Order.query.get(o.id).status == 'paid')

    # cleanup
    with app.app_context():
        for oi in OrderItem.query.filter_by(order_id=o.id).all():
            db.session.delete(oi)
        db.session.delete(Order.query.get(o.id))
        db.session.delete(Product.query.get(p.id))
        db.session.delete(User.query.filter_by(username='buyer_e2e').first())
        db.session.commit()
        print('\n  cleanup done')

print(f'\n{len(PASSED)} passed, {len(FAILED)} failed')
if FAILED:
    print('FAILED:', FAILED)
    sys.exit(1)
