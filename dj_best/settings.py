import json
import os
import socket
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


# =========================================================
# Dynamic settings loader
# Priority:
#   1) Environment variables
#   2) settings.json
#   3) default
# =========================================================
SETTINGS_FILE = BASE_DIR / "dj_best" / "settings.json"


def load_json_settings():
    if SETTINGS_FILE.exists():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


JSON_SETTINGS = load_json_settings()


def get_dynamic_setting(key, default=None):
    env_value = os.getenv(key)
    if env_value is not None:
        return env_value
    return JSON_SETTINGS.get(key, default)


def get_first_setting(keys, default=None):
    for key in keys:
        env_value = os.getenv(key)
        if env_value is not None:
            return env_value
        if key in JSON_SETTINGS:
            return JSON_SETTINGS.get(key)
    return default


def get_bool(key, default=False):
    value = get_dynamic_setting(key, default)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def get_bool_multi(keys, default=False):
    value = get_first_setting(keys, default)
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def get_int(key, default=0):
    value = get_dynamic_setting(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_int_multi(keys, default=0):
    value = get_first_setting(keys, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_list(key, default=None, sep=","):
    if default is None:
        default = []

    value = get_dynamic_setting(key, None)

    if value is None:
        return default

    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]

    return [item.strip() for item in str(value).split(sep) if item.strip()]


def unique_list(items):
    result = []
    seen = set()

    for item in items:
        value = str(item).strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)

    return result


AUTH_USER_MODEL = "profiles.user_profile"

cpanel_user = get_first_setting(["CPANEL_USER", "cpanel_user"], "")


# =========================================================
# Core security
# Compatible with deploy.sh generated .env
# =========================================================
SECRET_KEY = get_first_setting(
    ["DJANGO_SECRET_KEY", "SECRET_KEY"],
    "django-insecure-change-this-in-production"
)

DEBUG = get_bool_multi(["DJANGO_DEBUG", "DEBUG"], False)
TRACE = False
main_host = get_dynamic_setting("MAIN_HOST", "")

allowed_hosts_from_env = get_list("ALLOWED_HOSTS", [])

if main_host and "," not in str(main_host):
    ALLOWED_HOSTS = unique_list(
        allowed_hosts_from_env + [
            main_host,
            f"www.{main_host}",
            "localhost",
            "127.0.0.1",
        ]
    )
else:
    ALLOWED_HOSTS = unique_list(
        allowed_hosts_from_env + [
            "localhost",
            "127.0.0.1",
        ]
    )


# =========================================================
# Proxy / SSL settings for Nginx reverse proxy
# =========================================================
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
SECURE_SSL_REDIRECT = not DEBUG

if DEBUG:
    CSRF_COOKIE_SECURE = False
    SESSION_COOKIE_SECURE = False
    CSRF_COOKIE_HTTPONLY = False
    # Local development runs over plain HTTP. SameSite=None requires Secure
    # in modern browsers and can cause Chrome to reject the CSRF cookie.
    CSRF_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SAMESITE = "Lax"
else:
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_HTTPONLY = False
    CSRF_COOKIE_SAMESITE = "None"
    SESSION_COOKIE_SAMESITE = "Lax"

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

if not DEBUG:
    SECURE_HSTS_SECONDS = get_int("SECURE_HSTS_SECONDS", 31536000)
    SECURE_HSTS_INCLUDE_SUBDOMAINS = get_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", True)
    SECURE_HSTS_PRELOAD = get_bool("SECURE_HSTS_PRELOAD", True)
else:
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False


# =========================================================
# Applications
# =========================================================
INSTALLED_APPS = [
    # "django_daisy",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.humanize",

    "corsheaders",
    "import_export",
    "jalali_date",
    "csp",
    "adminsortable2",
    "django_user_agents",
    "ckeditor",
    "ckeditor_uploader",
    "django_json_widget",
    # "compressor",
]

if DEBUG:
    INSTALLED_APPS.append("django_browser_reload")


def is_django_app(path: Path):
    return (path / "apps.py").exists() and (path / "__init__.py").exists()


NEW_APP = [
    app.name for app in os.scandir(BASE_DIR)
    if app.is_dir() and is_django_app(Path(app.path))
]

INSTALLED_APPS += NEW_APP


# =========================================================
# Email
# =========================================================
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = get_dynamic_setting("EMAIL_HOST", "")
EMAIL_PORT = get_int("EMAIL_PORT", 587)
EMAIL_USE_TLS = get_bool("EMAIL_USE_TLS", True)
EMAIL_HOST_USER = get_dynamic_setting("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = get_dynamic_setting("EMAIL_HOST_PASSWORD", "")
EMAIL_TIMEOUT = get_int("EMAIL_TIMEOUT", 10)
DEFAULT_FROM_EMAIL = get_dynamic_setting("EMAIL_HOST_USER", "")
VAPID_PUBLIC_KEY = get_dynamic_setting("VAPID_PUBLIC_KEY", "")
VAPID_SUBJECT = get_dynamic_setting("VAPID_SUBJECT", "mailto:admin@amn-negar.com")


# Melipayamak
MELIPAYAMAK_USERNAME = os.environ.get("MELIPAYAMAK_USERNAME", '09136992155')
MELIPAYAMAK_PASSWORD = os.environ.get("MELIPAYAMAK_PASSWORD", '2bff08b4-7f4b-4aa7-b036-d3d7bbba68eb')
# ── Auth & Account ─────────────────────────────────────────────────────────
SMS_BODY_ID_OTP              = int(os.environ.get("SMS_BODY_ID_OTP", 520730))
SMS_BODY_ID_FORGOT_PASSWORD  = int(os.environ.get("SMS_BODY_ID_FORGOT_PASSWORD", 520736))
SMS_BODY_ID_CHANGE_PASSWORD  = int(os.environ.get("SMS_BODY_ID_CHANGE_PASSWORD", 520740))
SMS_BODY_ID_ACTIVATE_ACCOUNT = int(os.environ.get("SMS_BODY_ID_ACTIVATE_ACCOUNT", 520742))

# ── Ticket lifecycle ───────────────────────────────────────────────────────
SMS_BODY_ID_TICKET_CREATED        = int(os.environ.get("SMS_BODY_ID_TICKET_CREATED",        520745))
SMS_BODY_ID_TICKET_ASSIGNED_UNIT  = int(os.environ.get("SMS_BODY_ID_TICKET_ASSIGNED_UNIT",  520746))
SMS_BODY_ID_TICKET_ASSIGNED_AGENT = int(os.environ.get("SMS_BODY_ID_TICKET_ASSIGNED_AGENT", 520747))
SMS_BODY_ID_TICKET_NEW_REPLY      = int(os.environ.get("SMS_BODY_ID_TICKET_NEW_REPLY",      520748))
SMS_BODY_ID_TICKET_NEEDS_RESPONSE = int(os.environ.get("SMS_BODY_ID_TICKET_NEEDS_RESPONSE", 520749))
SMS_BODY_ID_TICKET_STATUS_CHANGED = int(os.environ.get("SMS_BODY_ID_TICKET_STATUS_CHANGED", 520750))
SMS_BODY_ID_TICKET_RESOLVED       = int(os.environ.get("SMS_BODY_ID_TICKET_RESOLVED",       520751))
SMS_BODY_ID_TICKET_CLOSED         = int(os.environ.get("SMS_BODY_ID_TICKET_CLOSED",         520752))
SMS_BODY_ID_TICKET_REOPENED       = int(os.environ.get("SMS_BODY_ID_TICKET_REOPENED",       520753))

# ── SLA ────────────────────────────────────────────────────────────────────
SMS_BODY_ID_SLA_WARNING = int(os.environ.get("SMS_BODY_ID_SLA_WARNING", 520755))
SMS_BODY_ID_SLA_BREACH  = int(os.environ.get("SMS_BODY_ID_SLA_BREACH",  520756))
SMS_BODY_ID_SLA_CHANGED = int(os.environ.get("SMS_BODY_ID_SLA_CHANGED", 520757))

# ── Visit / Field service ──────────────────────────────────────────────────
SMS_BODY_ID_VISIT_SCHEDULED = int(os.environ.get("SMS_BODY_ID_VISIT_SCHEDULED", 520758))
SMS_BODY_ID_VISIT_RESCHEDULED = int(os.environ.get("SMS_BODY_ID_VISIT_RESCHEDULED", 520759))
SMS_BODY_ID_VISIT_EN_ROUTE  = int(os.environ.get("SMS_BODY_ID_VISIT_EN_ROUTE",  520760))
SMS_BODY_ID_VISIT_STARTED   = int(os.environ.get("SMS_BODY_ID_VISIT_STARTED",   520761))
SMS_BODY_ID_VISIT_COMPLETED = int(os.environ.get("SMS_BODY_ID_VISIT_COMPLETED", 520762))
SMS_BODY_ID_VISIT_NEEDS_CONFIRM = int(os.environ.get("SMS_BODY_ID_VISIT_NEEDS_CONFIRM", 520763))

# ── Project ────────────────────────────────────────────────────────────────
SMS_BODY_ID_PROJECT_STARTED        = int(os.environ.get("SMS_BODY_ID_PROJECT_STARTED",        520764))
SMS_BODY_ID_PROJECT_STATUS_CHANGED = int(os.environ.get("SMS_BODY_ID_PROJECT_STATUS_CHANGED", 520765))
SMS_BODY_ID_PROJECT_DEADLINE_NEAR  = int(os.environ.get("SMS_BODY_ID_PROJECT_DEADLINE_NEAR",  520766))
SMS_BODY_ID_PROJECT_COMPLETED      = int(os.environ.get("SMS_BODY_ID_PROJECT_COMPLETED",      520768))

# ── Contract ───────────────────────────────────────────────────────────────
SMS_BODY_ID_CONTRACT_EXPIRY = int(os.environ.get("SMS_BODY_ID_CONTRACT_EXPIRY", 520769))

# ── Web Push ───────────────────────────────────────────────────────────────
VAPID_PRIVATE_KEY = os.environ.get("VAPID_PRIVATE_KEY", " ")
WEBPUSH_ADMIN_EMAIL = os.environ.get("WEBPUSH_ADMIN_EMAIL", "admin@example.com")


LOGIN_URL = "front:login_fun"
LOGOUT_REDIRECT_URL = "front:login_fun"

INTERNAL_IPS = [
    "127.0.0.1",
]


# =========================================================
# Middleware
# =========================================================
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.middleware.gzip.GZipMiddleware",

    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "api.middleware.ApiCsrfExemptionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",

    "django_user_agents.middleware.UserAgentMiddleware",
    "crum.CurrentRequestUserMiddleware",

    "api.middleware.AuthenticationMiddleware",
    "core.setting_middleware.RemoveDoubleSlashesMiddleware",

    "htmlmin.middleware.HtmlMinifyMiddleware",
    "htmlmin.middleware.MarkRequestMiddleware",
]

if DEBUG:
    MIDDLEWARE.append("django_browser_reload.middleware.BrowserReloadMiddleware")


DAISY_SETTINGS = {
    "SITE_TITLE": "پنل مدیریت",
    "SITE_HEADER": "پنل مدیریت",
    "INDEX_TITLE": "سلام به پنل ادمین خوش آمدید",
    "SITE_LOGO": "/static/admin/logo.png",
    "EXTRA_STYLES": [],
    "EXTRA_SCRIPTS": [],
    "LOAD_FULL_STYLES": True,
    "SHOW_CHANGELIST_FILTER": True,
    "DONT_SUPPORT_ME": True,
    "SIDEBAR_FOOTNOTE": "طراحی و توسعه امید پرداز کاسپین",
}

HTML_MINIFY = get_bool("HTML_MINIFY", True)
KEEP_COMMENTS_ON_MINIFYING = get_bool("KEEP_COMMENTS_ON_MINIFYING", False)
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

ROOT_URLCONF = "dj_best.urls"
WSGI_APPLICATION = "dj_best.wsgi.application"


# =========================================================
# Templates
# =========================================================
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# =========================================================
# Cache
# =========================================================
def redis_available(host="redis", port=6379):
    try:
        s = socket.create_connection((host, port), timeout=1)
        s.close()
        return True
    except Exception:
        return False


REDIS_URL = get_dynamic_setting("REDIS_URL", "")

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            },
        }
    }
elif DEBUG and redis_available():
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": "redis://redis:6379/0",
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
            },
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.filebased.FileBasedCache",
            "LOCATION": str(BASE_DIR / "cache-directory"),
            "TIMEOUT": 60 * 60 * 24,
            "OPTIONS": {
                "MAX_ENTRIES": 1000,
            },
        }
    }


ACTIVE_HTMLX = True

# =========================================================
# Database
# Compatible with both:
#   Local development:
#     DB_KIND=sqlite
#     DB_NAME=db.sqlite3                optional
#     SQLITE_PATH=/full/path/db.sqlite3 optional
#
#   Docker production:
#     DB_KIND=sqlite
#     DB_NAME=/app/data/db.sqlite3
#
#   PostgreSQL:
#     DB_KIND=postgres
#     DB_NAME=crm_db
#     DB_USER=crm_user
#     DB_PASSWORD=...
#     DB_HOST=172.17.0.1
#     DB_PORT=5432
# =========================================================
DB_KIND = str(get_dynamic_setting("DB_KIND", "sqlite")).strip().lower()
DATABASE_URL = str(get_dynamic_setting("DATABASE_URL", "")).strip()


def running_in_docker():
    return Path("/.dockerenv").exists() or os.getenv("RUNNING_IN_DOCKER") == "1"


def get_sqlite_name():
    explicit_sqlite_path = get_dynamic_setting("SQLITE_PATH", None)
    if explicit_sqlite_path:
        sqlite_path = str(explicit_sqlite_path).strip()

        if os.path.isabs(sqlite_path):
            return sqlite_path

        return str(BASE_DIR / sqlite_path)

    db_name = get_dynamic_setting("DB_NAME", None)

    if db_name:
        db_name = str(db_name).strip()

        if db_name.startswith("/app/"):
            if running_in_docker():
                return db_name
            return str(BASE_DIR / "db.sqlite3")

        if os.path.isabs(db_name):
            return db_name

        # For SQLite, DB_NAME must look like a file path/name.
        # Values like "dbname" are usually PostgreSQL logical database names.
        sqlite_suffixes = (".sqlite", ".sqlite3", ".db")
        if db_name.endswith(sqlite_suffixes):
            return str(BASE_DIR / db_name)

        return str(BASE_DIR / "db.sqlite3")

    return str(BASE_DIR / "db.sqlite3")

if DB_KIND == "mysql":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.mysql",
            "NAME": get_dynamic_setting("DB_NAME", ""),
            "USER": get_dynamic_setting("DB_USER", ""),
            "PASSWORD": get_dynamic_setting("DB_PASSWORD", ""),
            "HOST": get_dynamic_setting("DB_HOST", "localhost"),
            "PORT": get_int("DB_PORT", 3306),
            "OPTIONS": {
                "charset": "utf8mb4",
                "init_command": "SET sql_mode='STRICT_TRANS_TABLES'",
            },
        }
    }

elif DB_KIND in {"postgres", "postgresql"}:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": get_dynamic_setting("DB_NAME", ""),
            "USER": get_dynamic_setting("DB_USER", ""),
            "PASSWORD": get_dynamic_setting("DB_PASSWORD", ""),
            "HOST": get_dynamic_setting("DB_HOST", "localhost"),
            "PORT": get_int("DB_PORT", 5432),
            "CONN_MAX_AGE": get_int("DB_CONN_MAX_AGE", 600),
        }
    }

elif DATABASE_URL.startswith(("postgres://", "postgresql://", "mysql://", "sqlite:///")):
    try:
        import dj_database_url

        DATABASES = {
            "default": dj_database_url.parse(
                DATABASE_URL,
                conn_max_age=get_int("DB_CONN_MAX_AGE", 600),
                ssl_require=get_bool("DB_SSL_REQUIRE", False),
            )
        }
    except Exception:
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": get_sqlite_name(),
            }
        }

else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": get_sqlite_name(),
        }
    }


# =========================================================
# Password validators
# =========================================================
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# =========================================================
# Logging
# =========================================================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{levelname}] {asctime} {module}: {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "DEBUG" if DEBUG else "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": True,
        },
        "core.hooks": {
            "handlers": ["console"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
    },
}


# =========================================================
# Jalali / CKEditor / i18n
# =========================================================
JALALI_DATE_DEFAULTS = {
    "LIST_DISPLAY_AUTO_CONVERT": True,
    "Strftime": {
        "date": "%Y/%m/%d",
        "datetime": "%H:%M:%S _ %y/%m/%d",
    },
    "Static": {
        "js": [
            "admin/js/django_jalali.min.js",
        ],
        "css": {
            "all": [
                "admin/css/django_jalali.min.css",
            ]
        },
    },
}

CKEDITOR_BASEPATH = "/static/ckeditor/ckeditor/"
CKEDITOR_UPLOAD_PATH = "uploads/"
CKEDITOR_FILENAME_GENERATOR = "utils.get_filename"

LANGUAGE_CODE = get_dynamic_setting("LANGUAGE_CODE", "fa-ir")
TIME_ZONE = get_dynamic_setting("TIME_ZONE", "Asia/Tehran")
USE_I18N = True
USE_TZ = True


# =========================================================
# Static / Media
# Compatible with deploy.sh Docker volumes:
#   django_static -> /app/staticfiles
#   django_media  -> /app/media
# =========================================================
STATIC_URL = get_dynamic_setting("STATIC_URL", "/static/")
MEDIA_URL = get_dynamic_setting("MEDIA_URL", "/media/")

STATIC_ROOT = get_dynamic_setting("STATIC_ROOT", "/app/staticfiles")
MEDIA_ROOT = get_dynamic_setting("MEDIA_ROOT", "/app/media")

STATICFILES_DIRS = [
    str(BASE_DIR / "static"),
]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

COMPRESS_ROOT = BASE_DIR / "static"
COMPRESS_ENABLED = get_bool("COMPRESS_ENABLED", True)

STATICFILES_FINDERS = (
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    # "compressor.finders.CompressorFinder",
)

# =========================================================
# CORS / CSRF for Nuxt frontend
# Compatible with deploy.sh:
#   CORS_ALLOWED_ORIGINS=https://crm.amn-negar.com,https://panel.crm.amn-negar.com
#   CSRF_TRUSTED_ORIGINS=https://panel.crm.amn-negar.com,https://crm.amn-negar.com
# =========================================================
CORS_ALLOW_CREDENTIALS = True

# Keep the CSRF cookie host-only by default. A fixed production domain here
# prevents Django admin on localhost/127.0.0.1 from setting csrftoken.
# The value can still be overridden for a shared production domain.

# on delpoy:
# CSRF_COOKIE_DOMAIN = ".crm.amn-negar.com"

# Never inherit a production cookie domain during local development.
# A domain such as .crm.amn-negar.com is invalid for 127.0.0.1/localhost.
if DEBUG:
    CSRF_COOKIE_DOMAIN = None
else:
    csrf_cookie_domain = get_dynamic_setting("CSRF_COOKIE_DOMAIN", "").strip()
    CSRF_COOKIE_DOMAIN = csrf_cookie_domain or None

CORS_EXPOSE_HEADERS = [
    "Content-Type",
    "X-CSRFToken",
]

CSRF_COOKIE_NAME = "csrftoken"
CSRF_HEADER_NAME = "HTTP_X_CSRFTOKEN"

default_frontend_origins = []

if DEBUG:
    default_frontend_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://172.23.20.9:3000",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
    ]

env_cors_origins = get_list("CORS_ALLOWED_ORIGINS", [])
env_csrf_origins = get_list("CSRF_TRUSTED_ORIGINS", [])

CORS_ALLOWED_ORIGINS = unique_list(default_frontend_origins + env_cors_origins)
CSRF_TRUSTED_ORIGINS = unique_list(default_frontend_origins + env_csrf_origins)

if main_host and "," not in str(main_host):
    production_origins = [
        f"http://{main_host}",
        f"https://{main_host}",
        f"http://www.{main_host}",
        f"https://www.{main_host}",
    ]

    CORS_ALLOWED_ORIGINS = unique_list(CORS_ALLOWED_ORIGINS + production_origins)
    CSRF_TRUSTED_ORIGINS = unique_list(CSRF_TRUSTED_ORIGINS + production_origins)

CORS_ALLOW_HEADERS = [
    "accept",
    "authorization",
    "content-type",
    "user-agent",
    "x-csrftoken",
    "x-requested-with",
]
