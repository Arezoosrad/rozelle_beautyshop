import os
from pathlib import Path
BASE_DIR=Path(__file__).resolve().parent.parent
env=lambda k,d=None: os.getenv(k,d)
SECRET_KEY=env("DJANGO_SECRET_KEY","dev-only-change-me")
DEBUG=env("DJANGO_DEBUG","false").lower() in {"1","true","yes"}
ALLOWED_HOSTS=[x.strip() for x in env("ALLOWED_HOSTS","localhost,127.0.0.1").split(",") if x.strip()]
INSTALLED_APPS=["django.contrib.admin","django.contrib.auth","django.contrib.contenttypes","django.contrib.sessions","django.contrib.messages","django.contrib.staticfiles","rest_framework","django_filters","core","api","accounts","catalog","inventory","cart","orders","payments","shipping","promotions","reviews"]
MIDDLEWARE=["django.middleware.security.SecurityMiddleware","whitenoise.middleware.WhiteNoiseMiddleware","django.contrib.sessions.middleware.SessionMiddleware","django.middleware.common.CommonMiddleware","django.middleware.csrf.CsrfViewMiddleware","django.contrib.auth.middleware.AuthenticationMiddleware","django.contrib.messages.middleware.MessageMiddleware","django.middleware.clickjacking.XFrameOptionsMiddleware"]
ROOT_URLCONF="dj_best.urls"
TEMPLATES=[{"BACKEND":"django.template.backends.django.DjangoTemplates","DIRS":[BASE_DIR/"templates"],"APP_DIRS":True,"OPTIONS":{"context_processors":["django.template.context_processors.request","django.contrib.auth.context_processors.auth","django.contrib.messages.context_processors.messages"]}}]
WSGI_APPLICATION="dj_best.wsgi.application"; ASGI_APPLICATION="dj_best.asgi.application"
DB_KIND=env("DB_KIND","sqlite").lower()
DATABASES={"default":{"ENGINE":"django.db.backends.postgresql","NAME":env("DB_NAME","rozelle"),"USER":env("DB_USER","postgres"),"PASSWORD":env("DB_PASSWORD",""),"HOST":env("DB_HOST","localhost"),"PORT":env("DB_PORT","5432"),"CONN_MAX_AGE":600}} if DB_KIND in {"postgres","postgresql"} else {"default":{"ENGINE":"django.db.backends.sqlite3","NAME":BASE_DIR/"db.sqlite3"}}
REDIS_URL=env("REDIS_URL","redis://localhost:6379/0")
CACHES={"default":{"BACKEND":"django_redis.cache.RedisCache","LOCATION":REDIS_URL,"OPTIONS":{"CLIENT_CLASS":"django_redis.client.DefaultClient"}}}
REST_FRAMEWORK={"DEFAULT_FILTER_BACKENDS":["django_filters.rest_framework.DjangoFilterBackend","rest_framework.filters.SearchFilter","rest_framework.filters.OrderingFilter"],"DEFAULT_PAGINATION_CLASS":"rest_framework.pagination.PageNumberPagination","PAGE_SIZE":24}
CELERY_BROKER_URL=env("CELERY_BROKER_URL",REDIS_URL); CELERY_RESULT_BACKEND=env("CELERY_RESULT_BACKEND",REDIS_URL)
AUTH_USER_MODEL="accounts.User"
LANGUAGE_CODE="fa-ir"; TIME_ZONE="Asia/Tehran"; USE_I18N=True; USE_TZ=True
STATIC_URL="/static/"; STATIC_ROOT=BASE_DIR/"staticfiles"; MEDIA_URL="/media/"; MEDIA_ROOT=BASE_DIR/"media"; DEFAULT_AUTO_FIELD="django.db.models.BigAutoField"
