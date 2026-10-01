import hashlib
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

SECRET_KEY = os.environ.get('SECRET_KEY', 'safferon-felahati-secret-key-2024')
SQLALCHEMY_DATABASE_URI = 'mysql+mysqlconnector://root:1@localhost:3306/felahati_safferon'
SQLALCHEMY_TRACK_MODIFICATIONS = False
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'Safferon@2024'
ZARINPAL_MERCHANT_ID = os.environ.get('ZARINPAL_MERCHANT_ID', 'test_merchant_id')
ZARINPAL_SANDBOX_KEY = os.environ.get('ZARINPAL_SANDBOX_KEY', '')
ZARINPAL_SANDBOX_API = 'https://sandbox.zarinpal.com/pg/rest/v4/payment'
