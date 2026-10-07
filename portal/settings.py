import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_bool(name, default=False):
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "development-only-insecure-key")
DEBUG = env_bool("DJANGO_DEBUG", True)
ALLOWED_HOSTS = [
    item.strip()
    for item in os.getenv("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost").split(",")
    if item.strip()
]
CSRF_TRUSTED_ORIGINS = [
    item.strip() for item in os.getenv("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",") if item.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "results",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "portal.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "results.context_processors.portal",
            ],
        },
    }
]
WSGI_APPLICATION = "portal.wsgi.application"

if os.getenv("DB_ENGINE") == "postgresql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ["DB_NAME"],
            "USER": os.environ["DB_USER"],
            "PASSWORD": os.environ["DB_PASSWORD"],
            "HOST": os.getenv("DB_HOST", "127.0.0.1"),
            "PORT": os.getenv("DB_PORT", "5432"),
            "CONN_MAX_AGE": 60,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = os.getenv("PORTAL_TIME_ZONE", "Asia/Karachi")
USE_I18N = True
USE_TZ = True

STATIC_URL = "/portal/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

LOGIN_URL = "/portal/login/"
LOGIN_REDIRECT_URL = "/portal/"
LOGOUT_REDIRECT_URL = "/portal/login/"

PORTAL_INGEST_TOKEN = os.getenv("PORTAL_INGEST_TOKEN", "")
PORTAL_ARTIFACT_ROOT = Path(
    os.getenv("PORTAL_ARTIFACT_ROOT", str(BASE_DIR / "artifact-store"))
).resolve()
MEDIA_ROOT = Path(os.getenv("PORTAL_UPLOAD_ROOT", str(BASE_DIR / "uploads"))).resolve()
PORTAL_ELF_UPLOAD_MAX_BYTES = int(os.getenv("PORTAL_ELF_UPLOAD_MAX_BYTES", str(32 * 1024 * 1024)))
PORTAL_SINGLE_ELF_BOARD_SLUGS = tuple(
    item.strip()
    for item in os.getenv(
        "PORTAL_SINGLE_ELF_BOARD_SLUGS", "visionfive2,bananapi-f3,milkv-megrez"
    ).split(",")
    if item.strip()
)
JENKINS_TRIGGER_URL = os.getenv("JENKINS_TRIGGER_URL", "")
JENKINS_TRIGGER_USER = os.getenv("JENKINS_TRIGGER_USER", "")
JENKINS_TRIGGER_TOKEN = os.getenv("JENKINS_TRIGGER_TOKEN", "")
JENKINS_SINGLE_ELF_JOB = os.getenv("JENKINS_SINGLE_ELF_JOB", "riscv-uart-single-elf")
PORTAL_NAME = os.getenv(
    "PORTAL_NAME", "RISC-V Architectural Compliance & Post-Silicon Regression Platform"
)
JENKINS_CA_FILE = os.getenv("JENKINS_CA_FILE", "")
PORTAL_EXTERNAL_URL = os.getenv("PORTAL_EXTERNAL_URL", "")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", not DEBUG)
SESSION_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", not DEBUG)
CSRF_COOKIE_SECURE = env_bool("DJANGO_SECURE_COOKIES", not DEBUG)
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
