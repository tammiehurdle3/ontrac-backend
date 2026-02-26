# ontrac_project/settings.py

from pathlib import Path
import os
import environ
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Initialize environment variables
env = environ.Env()
environ.Env.read_env()


# --- START: ENVIRONMENT-AWARE CONFIGURATION ---

ENVIRONMENT = env('ENVIRONMENT', default='production')
SECRET_KEY = env('SECRET_KEY')
DEBUG = env.bool('DEBUG', default=(ENVIRONMENT == 'local'))

# --- NEW: DEBUGGING STATEMENTS ---
# These will print to your Render logs so we can see what's happening.
print("--- STARTING DEPLOYMENT LOG ---")
print(f"[*] Environment detected: {ENVIRONMENT}")
print(f"[*] DATABASE_URL found: {env('DATABASE_URL', default='NOT FOUND')}")
print("-----------------------------")
# ------------------------------------

if ENVIRONMENT == 'local':
    print("✅ Running with LOCAL settings and SQLite database.")
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
    supabase = None

else:
    print("🚀 Running with PRODUCTION settings.")
    DATABASES = {
        'default': env.db(),
    }
    DATABASES['default']['CONN_MAX_AGE'] = 0
    SUPABASE_API_URL = env('SUPABASE_API_URL')
    SUPABASE_SERVICE_KEY = env('SUPABASE_SERVICE_KEY')
    supabase: Client = create_client(SUPABASE_API_URL, SUPABASE_SERVICE_KEY)

# --- END: CONFIGURATION ---


ALLOWED_HOSTS = [
    'ontrac-backend.onrender.com',
    'www.ontracourier.us',
    'ontracourier.us',
    'ontrac-backend-ru7g.onrender.com',
    '127.0.0.1',
    'localhost',
    '192.168.1.246',
    '316c-104-234-32-179.ngrok-free.app',
    'a69b-136-144-33-243.ngrok-free.app',
]

# ... (The rest of your settings file remains exactly the same) ...
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'api',
    'rest_framework',
    'corsheaders',
    'django_redis',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    
]

ROOT_URLCONF = 'ontrac_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'ontrac_project.wsgi.application'

AUTH_PASSWORD_VALIDATORS = [
    { 'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator', },
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:8001",
    "https://www.ontracourier.us",
    "https://ontracourier.us",
    "https://zesty-klepon-86a44e.netlify.app",
    "https://ontrac-react.netlify.app"
]
# --- START: New setting for Webhook Security ---
# This tells Django to trust POST requests coming from your ngrok URL
CSRF_TRUSTED_ORIGINS = [
    'https://ec5125113bcf.ngrok-free.app',
    'https://316c-104-234-32-179.ngrok-free.app',
    'https://a69b-136-144-33-243.ngrok-free.app',
    'https://ontrac-backend-ru7g.onrender.com',
]
# --- SECURE API KEY CONFIGURATION ---
# FIX: Safely loading keys from your .env file
EXCHANGE_RATE_API_KEY = env('EXCHANGE_RATE_API_KEY', default='')

# FIX: Safely loading keys from your .env file
PUSHER_APP_ID = env('PUSHER_APP_ID')
PUSHER_KEY = env('PUSHER_KEY')
PUSHER_SECRET = env('PUSHER_SECRET')
PUSHER_CLUSTER = env('PUSHER_CLUSTER')

#BREVO_API_KEY = env('BREVO_API_KEY', default='')
#MAILERSEND TO REPLACEE BREVO, TRANSACTIONAL EMAILS FOR ONTRAC
MAILERSEND_API_KEY = env('MAILERSEND_API_KEY', default='')

# RESEND FOR TRANSACTIONAL EMAILS
RESEND_API_KEY = env('RESEND_API_KEY', default='')

#MILANI INITIAL OUTREACH
SENDGRID_API_KEY = env('SENDGRID_API_KEY', default='')
SENDGRID_TRANSACTIONAL_API_KEY = env('SENDGRID_TRANSACTIONAL_API_KEY', default='')

# --- DJANGO CACHE CONFIGURATION ---
# This tells Django to use your Render Redis as its cache
CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": env('REDIS_URL', default='redis://localhost:6379/1'),
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
        }
    }
}

# ============================================================================
# SHIELDCLIMB PAYMENT GATEWAY CONFIGURATION
# ============================================================================

# Your Polygon USDC payout wallet address
SHIELDCLIMB_PAYOUT_WALLET = env(
    'SHIELDCLIMB_PAYOUT_WALLET',
    default='0x5246ecaff77bBAE8Ba50Ff3664bB1Ee9E23d7cAE'
)

# Callback base URL (ShieldClimb will send GET requests here)
# Development: http://127.0.0.1:8000
# Production: https://ontrac-backend-ru7g.onrender.com
SHIELDCLIMB_CALLBACK_BASE_URL = env(
    'SHIELDCLIMB_CALLBACK_BASE_URL',
    default='https://ontrac-backend-ru7g.onrender.com'
)

# White-label custom domain (optional)
# Set this to your custom subdomain for a fully branded experience
SHIELDCLIMB_CUSTOM_DOMAIN = env(
    'SHIELDCLIMB_CUSTOM_DOMAIN',
    default='pay.ontracourier.us'
)

# Brand logo URL for checkout page
SHIELDCLIMB_LOGO_URL = env(
    'SHIELDCLIMB_LOGO_URL',
    default='https://ontracourier.us/ontrac_favicon.png'
)

# Theme color for checkout (brand blue instead of red for trust)
SHIELDCLIMB_THEME_COLOR = env(
    'SHIELDCLIMB_THEME_COLOR',
    default='#1778F2'  # Professional blue from your style.css
)