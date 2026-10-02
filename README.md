# Safferon Falahati

A Persian (RTL) online store for saffron.

## Tech Stack

- Python 3 / Flask 3
- Flask-SQLAlchemy with a MariaDB/MySQL database (mysql-connector-python driver)
- Flask-WTF for CSRF protection
- Plain HTML forms (no Flask-Form)
- Custom CSS (light red/white theme)
- eggy.js for toast notifications
- AJAX cart and live product search

## Project Layout

```
app.py          app factory, routes and views
config.py       flat configuration (DB URI, admin credentials)
extensions.py   shared db and csrf instances
func.py         helper functions (auth, discounts, parsing, Persian output)
models/         SQLAlchemy models only (no custom methods)
templates/      Jinja templates (public + admin)
static/         css, js, fonts, images
wsgi.py         WSGI entry point
```

## Features

- Customer accounts: register, login (90-day persistent session), profile
- Product catalog with live search and a discount engine (quantity-based tiers)
- Cart with AJAX update/remove and a live navbar counter
- Checkout and a mock (test-only) payment flow
- Admin panel (no admin table; credentials live in config.py): products,
  discounts, orders with status changes, users, about-us and footer content
- Data stays consistent: orders are kept when a user is deleted, stock is
  restored when a paid order is cancelled

## Setup

1. Create the MariaDB/MySQL database and note its credentials.
2. Fill in `config.py` (SECRET_KEY, DB URI, admin username/password).
3. Install dependencies:

   ```
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

4. Run:

   ```
   python3 app.py
   ```

   The app creates tables on first start. Open http://127.0.0.1:5000.
   The admin panel is at /admin (no public link by design).
