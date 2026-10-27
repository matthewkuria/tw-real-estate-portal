import os
from datetime import timedelta
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Environment
# Values come from real environment variables. For local development they
# can also be put in backend/.env (git-ignored); see .env.example.

def _load_env_file(path):
    if not path.exists():
        return

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def env(name, default=None):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    return env(name, str(default)).lower() in {"1", "true", "yes"}


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


_load_env_file(BASE_DIR / ".env")


# See https://docs.djangoproject.com/en/6.1/howto/deployment/checklist/

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = env_bool("DJANGO_DEBUG")

# SECURITY WARNING: keep the secret key used in production secret!
# It also signs JWTs and password reset links.
SECRET_KEY = env("DJANGO_SECRET_KEY")

if not SECRET_KEY:
    raise ImproperlyConfigured("Set the DJANGO_SECRET_KEY environment variable.")

ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")

if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True


# Application definition

INSTALLED_APPS = [
    # Must come before staticfiles so runserver serves ASGI (WebSockets).
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'apps.payments',
    'apps.estates',
    'apps.accounts',
    'apps.notifications',
    'apps.audit',
    'apps.building_plans',
    'apps.properties',
    'apps.community',
    'apps.gallery',
    'apps.newsletters',
    'apps.advertisements',
    'channels',
    'corsheaders',
    # Rest Framework and DRF Spectacular for API documentation
    'rest_framework',
    'rest_framework_simplejwt',
    'rest_framework_simplejwt.token_blacklist',
    'drf_spectacular',
]

REST_FRAMEWORK = {
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",

    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),

    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),

    # Per-IP limits for the public auth endpoints (ScopedRateThrottle).
    "DEFAULT_THROTTLE_RATES": {
        "login": "10/minute",
        "register": "10/hour",
        "password_reset": "5/hour",
        "password_reset_confirm": "10/hour",
        # Per user: new forum threads and messages.
        "forum_post": "30/minute",
        # Per IP: the public newsletter unsubscribe link.
        "newsletter_unsubscribe": "30/minute",
    },
}
SPECTACULAR_SETTINGS = {
    "TITLE": "Real Estate Portal API",
    "DESCRIPTION": "API for the Real Estate Membership Portal",
    "VERSION": "1.0.0",

    "SERVE_INCLUDE_SCHEMA": False,

    # Several models have a "status" field with different choices; give
    # each its own name in the docs.
    "ENUM_NAME_OVERRIDES": {
        "UserStatusEnum": "apps.accounts.models.User.Status",
        "NewsletterStatusEnum": "apps.newsletters.models.Newsletter.Status",
        "AdvertisementCategoryEnum": "apps.advertisements.models.Advertisement.Category",
    },

    "SECURITY": [
        {
            "jwtAuth": [],
        },
    ],

    "APPEND_COMPONENTS": {
        "securitySchemes": {
            "jwtAuth": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
            },
        },
    },
}
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "AUTH_HEADER_TYPES": ("Bearer",),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

# Next.js frontend, used to build links sent in emails (e.g. password reset)
FRONTEND_URL = env("FRONTEND_URL", "http://localhost:3000").rstrip("/")
PASSWORD_RESET_PATH = "/reset-password"

# Public URL of this API, for links that must reach the backend directly
# (e.g. one-click unsubscribe from a mail app).
API_BASE_URL = env("API_BASE_URL", "http://localhost:8000").rstrip("/")

# Shown in newsletter emails.
SITE_NAME = env("SITE_NAME", "Real Estate Portal")

# Browser origins allowed to open WebSockets (the Next.js app).
WEBSOCKET_ALLOWED_ORIGINS = env_list("WEBSOCKET_ALLOWED_ORIGINS", FRONTEND_URL)

# Browser origins allowed to call the API (the portal and the public
# website). Auth uses the Authorization header, not cookies, so no
# credentials are allowed.
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS", FRONTEND_URL)

MIDDLEWARE = [
    # As high as possible, so even error responses carry CORS headers.
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'


# WebSockets (Django Channels)
# Redis is needed when running more than one server process; the in-memory
# layer only delivers events within a single process.

if env("REDIS_URL"):
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [env("REDIS_URL")]},
        },
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        },
    }


# Database
# https://docs.djangoproject.com/en/6.1/ref/settings/#databases

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.1/ref/settings/#auth-password-validators

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

AUTH_USER_MODEL = "accounts.User"
# Internationalization
# https://docs.djangoproject.com/en/6.1/topics/i18n/

LANGUAGE_CODE = 'en-us'

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.1/howto/static-files/

STATIC_URL = 'static/'


# Uploaded files (gallery images)
# Served by Django only when DEBUG is on; in production serve MEDIA_ROOT
# from the web server or object storage, and point MEDIA_URL at it.

MEDIA_URL = env("MEDIA_URL", "/media/")
MEDIA_ROOT = Path(env("MEDIA_ROOT", str(BASE_DIR / "media")))

GALLERY_MAX_UPLOAD_MB = int(env("GALLERY_MAX_UPLOAD_MB", "10"))


# Email
# https://docs.djangoproject.com/en/6.1/topics/email/#topic-email-configuration

DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "webmaster@localhost")

# Real SMTP when EMAIL_HOST is set, otherwise print emails to the console.
if env("EMAIL_HOST"):
    MAILERS = {
        'default': {
            'BACKEND': 'django.core.mail.backends.smtp.EmailBackend',
            'OPTIONS': {
                'host': env("EMAIL_HOST"),
                'port': int(env("EMAIL_PORT", "587")),
                'username': env("EMAIL_HOST_USER", ""),
                'password': env("EMAIL_HOST_PASSWORD", ""),
                'use_tls': env_bool("EMAIL_USE_TLS", True),
            },
        },
    }
else:
    MAILERS = {
        'default': {
            'BACKEND': 'django.core.mail.backends.console.EmailBackend',
        },
    }
