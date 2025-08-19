# ontrac_project/settings.py

from pathlib import Path
import os
import environ
from supabase import create_client, Client # 1. ADD THIS IMPORT

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Initialize environment variables
env = environ.Env()
environ.Env.read_env() # Reads the .env file if it exists

# 2. ADD THIS BLOCK TO CONFIGURE SUPABASE CLIENT
# It reads the keys from your .env file
SUPABASE_API_URL = env('SUPABASE_API_URL')
SUPABASE_SERVICE_KEY = env('SUPABASE_SERVICE_KEY')
supabase: Client = create_client(SUPABASE_API_URL, SUPABASE_SERVICE_KEY)

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = env('SECRET_KEY') # Use the .env file for this too!

# SECURITY WARNING: don't run with debug turned on in production!
# Render will set this to False automatically if the key exists.
DEBUG = 'RENDER' not in os.environ

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

# 3. FIX THIS DATABASE BLOCK
# This now ONLY uses the DATABASE_URL from your .env file.
# It has no hardcoded default value, which is safer and ensures
# you are always using the correct Connection Pooler on Render.
DATABASES = {
    'default': env.db_url("DATABASE_URL")
}

# Password validation
# ... (the rest of your file is fine) ...
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
    "https://zesty-klepon-86a44e.netlify.app"
    "https://ontrac-react.netlify.app"
]