import os
import secrets
from pathlib import Path

DEBUG = False
# No signed cookies or password reset URLs are issued. Bearer sessions use database digests.
SECRET_KEY = secrets.token_urlsafe(64)
ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
if os.environ.get("KONGGU_CLOUD_HOST"):
    ALLOWED_HOSTS.append(os.environ["KONGGU_CLOUD_HOST"])
INSTALLED_APPS = ["django.contrib.auth", "django.contrib.contenttypes", "storage"]
ROOT_URLCONF = "urls"
MIDDLEWARE = ["storage.views.PrivateResponses"]
DATABASES = {"default": {
    "ENGINE": "django.db.backends.sqlite3",
    "NAME": Path(os.environ["KONGGU_CLOUD_DATA"]) / "konggu.sqlite3",
    "OPTIONS": {"timeout": 25, "transaction_mode": "IMMEDIATE"},
}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "zh-hans"
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 10}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
MAX_BODY_BYTES = MAX_DOCUMENT_BYTES + 64 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_BODY_BYTES
# Avoid persisting account names, Authorization headers, URLs or timetable contents.
LOGGING = {"version": 1, "disable_existing_loggers": False,
           "handlers": {"null": {"class": "logging.NullHandler"}},
           "loggers": {"django.request": {"handlers": ["null"], "propagate": False},
                       "django.security": {"handlers": ["null"], "propagate": False}}}
