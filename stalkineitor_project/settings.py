from datetime import timedelta
from pathlib import Path

from decouple import config, Csv
from kombu import Queue
import dj_database_url
import os

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Quick-start development settings - unsuitable for production
SECRET_KEY = config('SECRET_KEY')

DEBUG = config('DEBUG', default=False, cast=bool)

ALLOWED_HOSTS = config('ALLOWED_HOSTS', default=[], cast=Csv())

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    
    # Third party
    'django_htmx', # Uncomment if using HTMX later
    
    # Local
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'django_htmx.middleware.HtmxMiddleware',
]

ROOT_URLCONF = 'stalkineitor_project.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'], # Pointing to templates dir
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

WSGI_APPLICATION = 'stalkineitor_project.wsgi.application'

# Database
# Uses DATABASE_URL from .env
DATABASES = {
    'default': dj_database_url.config(
        default=config('DATABASE_URL', default='sqlite:///db.sqlite3')
    )
}

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Sao_Paulo'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = 'static/'

# Default primary key field type
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Celery Configuration
CELERY_BROKER_URL = config('CELERY_BROKER_URL', default='redis://localhost:6379/0')
CELERY_RESULT_BACKEND = config('CELERY_RESULT_BACKEND', default='redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TASK_SERIALIZER = 'json'
CELERY_RESULT_SERIALIZER = 'json'
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_DEFAULT_QUEUE = 'celery'
CELERY_TASK_QUEUES = (
    Queue('celery'),
    Queue('ratings'),
)
CELERY_TASK_ROUTES = {
    'core.tasks.refresh_problem_rating_cache': {'queue': 'ratings'},
}
CELERY_TASK_ANNOTATIONS = {
    'core.tasks.refresh_problem_rating_cache': {'rate_limit': '10/m'},
}
CELERY_BEAT_SCHEDULE = {
    'sync-students-every-10-min': {
        'task': 'core.tasks.sync_all_students',
        'schedule': timedelta(minutes=10),
    },
    'refresh-rating-stats-daily': {
        'task': 'core.tasks.recompute_rating_stats',
        'schedule': timedelta(days=1),
    },
    'refresh-score-windows-daily': {
        'task': 'core.tasks.recompute_score_windows',
        'schedule': timedelta(days=1),
    },
    'snapshot-rankings-daily': {
        'task': 'core.tasks.snapshot_rankings_task',
        'schedule': timedelta(days=1),
    },
}

CLIST_API_URL = config('CLIST_API_URL', default='https://clist.by/api/v4')
CLIST_USERNAME = config('CLIST_USERNAME', default='')
CLIST_API_KEY = config('CLIST_API_KEY', default='')
CLIST_TIMEOUT_SECONDS = config('CLIST_TIMEOUT_SECONDS', default=10, cast=int)
CLIST_CACHE_TTL_HOURS = config('CLIST_CACHE_TTL_HOURS', default=24, cast=int)
RATING_NORMALIZE_MIN = config('RATING_NORMALIZE_MIN', default=800, cast=int)
RATING_NORMALIZE_MAX = config('RATING_NORMALIZE_MAX', default=3500, cast=int)
