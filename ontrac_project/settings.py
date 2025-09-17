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
    SUPABASE_API_URL = env('SUPABASE_API_URL')
    SUPABASE_SERVICE_KEY = env('SUPABASE_SERVICE_KEY')
    supabase: Client = create_client(SUPABASE_API_URL, SUPABASE_SERVICE_KEY)

# --- END: CONFIGURATION ---


ALLOWED_HOSTS = [
    'ontrac-backend.onrender.com',
    'www.ontracourier.us',
    'ontracourier.us',
    'ontrac-backend-eehg.onrender.com',
    '127.0.0.1',
    'localhost',
    '192.168.1.246'
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
    "https://www.ontracourier.us",
    "https://ontracourier.us",
    "https://zesty-klepon-86a44e.netlify.app",
    "https://ontrac-react.netlify.app"
]
# --- SECURE API KEY CONFIGURATION ---
# FIX: Safely loading keys from your .env file
EXCHANGE_RATE_API_KEY = env('EXCHANGE_RATE_API_KEY', default='')

# FIX: Safely loading keys from your .env file
PUSHER_APP_ID = env('PUSHER_APP_ID')
PUSHER_KEY = env('PUSHER_KEY')
PUSHER_SECRET = env('PUSHER_SECRET')
PUSHER_CLUSTER = env('PUSHER_CLUSTER')

BREVO_API_KEY = env('BREVO_API_KEY', default='')