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
    'core.middleware.ProfileMiddleware',
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
STATICFILES_DIRS = [BASE_DIR / 'static']

# Session/CSRF security
SESSION_COOKIE_HTTPONLY = True
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True

# Feature flags
ENABLE_CODE_FORMATTER = True

# Auth redirects
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/login/'

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
    Queue('sync_fast'),
    Queue('rating_slow'),
)
CELERY_TASK_ROUTES = {
    'core.tasks.refresh_problem_rating_cache': {'queue': 'rating_slow'},
    'core.tasks.sync_contest_problems': {'queue': 'sync_fast'},
    'core.tasks.contests_catalog_refresh': {'queue': 'sync_fast'},
    'core.tasks.contests_problems_scheduler': {'queue': 'sync_fast'},
    'core.tasks.ratings_backfill_scheduler': {'queue': 'sync_fast'},
    'core.tasks.process_rating_fetch_jobs': {'queue': 'rating_slow'},
}
CELERY_TASK_ANNOTATIONS = {
    'core.tasks.refresh_problem_rating_cache': {'rate_limit': '10/m'},
}
CELERY_BEAT_SCHEDULE = {
    'sync-students-every-10-min': {
        'task': 'core.tasks.sync_all_students',
        'schedule': timedelta(minutes=10),
    },
    'contests-catalog-refresh': {
        'task': 'core.tasks.contests_catalog_refresh',
        'schedule': timedelta(hours=2),
    },
    'contests-problems-scheduler': {
        'task': 'core.tasks.contests_problems_scheduler',
        'schedule': timedelta(minutes=15),
    },
    'ratings-backfill-scheduler': {
        'task': 'core.tasks.ratings_backfill_scheduler',
        'schedule': timedelta(minutes=2),
    },
    'rating-fetch-jobs': {
        'task': 'core.tasks.process_rating_fetch_jobs',
        'schedule': timedelta(minutes=1),
    },
    'refresh-rating-stats-daily': {
        'task': 'core.tasks.recompute_rating_stats',
        'schedule': timedelta(days=1),
    },
    'refresh-score-windows-daily': {
        'task': 'core.tasks.recompute_score_windows',
        'schedule': timedelta(days=1),
    },
    'refresh-rating-conversion-daily': {
        'task': 'core.tasks.recompute_rating_conversion',
        'schedule': timedelta(days=1),
    },
    'snapshot-rankings-daily': {
        'task': 'core.tasks.snapshot_rankings_task',
        'schedule': timedelta(days=1),
    },
    'refresh-cf-rating-history-daily': {
        'task': 'core.tasks.refresh_all_cf_rating_history',
        'schedule': timedelta(days=1),
    },
    'snapshot-atcoder-ratings-daily': {
        'task': 'core.tasks.snapshot_atcoder_ratings',
        'schedule': timedelta(days=1),
    },
}

CLIST_API_URL = config('CLIST_API_URL', default='https://clist.by/api/v4')
CLIST_USERNAME = config('CLIST_USERNAME', default='')
CLIST_API_KEY = config('CLIST_API_KEY', default='')
CLIST_TIMEOUT_SECONDS = config('CLIST_TIMEOUT_SECONDS', default=10, cast=int)
CLIST_CACHE_TTL_HOURS = config('CLIST_CACHE_TTL_HOURS', default=24, cast=int)
RATING_PRIORITY_MAX = config('RATING_PRIORITY_MAX', default=1800, cast=int)
CONTEST_CATALOG_INCLUDE_ALL_TIME = config('CONTEST_CATALOG_INCLUDE_ALL_TIME', default=True, cast=bool)
CONTEST_CATALOG_KEEP_RECENT_YEARS = config('CONTEST_CATALOG_KEEP_RECENT_YEARS', default=2, cast=int)
CONTEST_CATALOG_HISTORY_BATCH_SIZE = config('CONTEST_CATALOG_HISTORY_BATCH_SIZE', default=1, cast=int)
CONTEST_CATALOG_START_YEAR_CF = config('CONTEST_CATALOG_START_YEAR_CF', default=2010, cast=int)
CONTEST_CATALOG_START_YEAR_AC = config('CONTEST_CATALOG_START_YEAR_AC', default=2014, cast=int)
RATING_NORMALIZE_MIN = config('RATING_NORMALIZE_MIN', default=800, cast=int)
RATING_NORMALIZE_MAX = config('RATING_NORMALIZE_MAX', default=3500, cast=int)
MANUAL_RATING_REFRESH_MINUTES = config('MANUAL_RATING_REFRESH_MINUTES', default=60, cast=int)
CF_SYNC_RECENT_MAX_COUNT = config('CF_SYNC_RECENT_MAX_COUNT', default=1000, cast=int)
CF_SYNC_INITIAL_MAX_COUNT = config('CF_SYNC_INITIAL_MAX_COUNT', default=5000, cast=int)
FORCE_CONTEST_SYNC_GLOBAL_LOCK_SECONDS = config('FORCE_CONTEST_SYNC_GLOBAL_LOCK_SECONDS', default=60, cast=int)
FORCE_CONTEST_SYNC_USER_LOCK_SECONDS = config('FORCE_CONTEST_SYNC_USER_LOCK_SECONDS', default=180, cast=int)
FORCE_CONTEST_SYNC_RATINGS_LIMIT_ADMIN = config('FORCE_CONTEST_SYNC_RATINGS_LIMIT_ADMIN', default=10, cast=int)
FORCE_CONTEST_SYNC_RATINGS_LIMIT_USER = config('FORCE_CONTEST_SYNC_RATINGS_LIMIT_USER', default=3, cast=int)
FORCE_CONTEST_SYNC_COOLDOWN_MINUTES_ADMIN = config('FORCE_CONTEST_SYNC_COOLDOWN_MINUTES_ADMIN', default=30, cast=int)
FORCE_CONTEST_SYNC_COOLDOWN_MINUTES_USER = config('FORCE_CONTEST_SYNC_COOLDOWN_MINUTES_USER', default=45, cast=int)
FORCE_CONTEST_SYNC_MAX_ATTEMPTS_ADMIN = config('FORCE_CONTEST_SYNC_MAX_ATTEMPTS_ADMIN', default=6, cast=int)
FORCE_CONTEST_SYNC_MAX_ATTEMPTS_USER = config('FORCE_CONTEST_SYNC_MAX_ATTEMPTS_USER', default=4, cast=int)
FORCE_CONTEST_SYNC_INLINE_MAX_STUDENTS_ADMIN = config('FORCE_CONTEST_SYNC_INLINE_MAX_STUDENTS_ADMIN', default=25, cast=int)
FORCE_CONTEST_SYNC_INLINE_MAX_STUDENTS_USER = config('FORCE_CONTEST_SYNC_INLINE_MAX_STUDENTS_USER', default=0, cast=int)
FORCE_CONTEST_SYNC_ALLOW_SUBMISSIONS_FOR_USERS = config('FORCE_CONTEST_SYNC_ALLOW_SUBMISSIONS_FOR_USERS', default=False, cast=bool)
