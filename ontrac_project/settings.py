# ontrac_project/settings.py

from pathlib import Path
import os
import environ
from supabase import create_client, Client
from dotenv import load_dotenv # <-- ADD THIS LINE

load_dotenv() # <-- AND ADD THIS LINE

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Initialize environment variables
env = environ.Env()
environ.Env.read_env() # Reads the .env file if it exists


# --- START: NEW ENVIRONMENT-AWARE CONFIGURATION ---
# This single block replaces the old SECRET_KEY, DEBUG, DATABASES,
# and Supabase client blocks. It's safer and more flexible.

# 1. Read the environment variable. Default to 'production' for safety.
ENVIRONMENT = env('ENVIRONMENT', default='production')

# 2. Set SECRET_KEY and DEBUG based on the environment
SECRET_KEY = env('SECRET_KEY')
DEBUG = env.bool('DEBUG', default=(ENVIRONMENT == 'local'))

# 3. Conditionally configure the database and Supabase client
if ENVIRONMENT == 'local':
    # --- LOCAL SETTINGS ---
    print("✅ Running with LOCAL settings and SQLite database.")
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }
    supabase = None # Supabase client is not needed for local DB work

else:
    # --- PRODUCTION (SUPABASE/RENDER) SETTINGS ---
    print("🚀 Running with PRODUCTION settings.")
    DATABASES = {
        'default': env.db(), # Reads DATABASE_URL from environment
    }
    SUPABASE_API_URL = env('SUPABASE_API_URL')
    SUPABASE_SERVICE_KEY = env('SUPABASE_SERVICE_KEY')
    supabase: Client = create_client(SUPABASE_API_URL, SUPABASE_SERVICE_KEY)

# --- END: NEW ENVIRONMENT-AWARE CONFIGURATION ---


ALLOWED_HOSTS = [
    'ontrac-backend.onrender.com',
    'www.ontracourier.us',
    'ontracourier.us',
    'ontrac-backend-eehg.onrender.com',
    '127.0.0.1',
    'localhost',
]

# Application definition
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
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'corsheaders.middleware.CorsMiddleware',
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

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    { 'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator', },
    { 'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator', },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

CORS_ALLOWED_ORIGINS = [
    "http://localhost:5173",
    "https://www.ontracourier.us",
    "https://ontracourier.us",
    "https://zesty-klepon-86a44e.netlify.app",
    "https://ontrac-react.netlify.app"
]
