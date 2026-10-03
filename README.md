# Rozelle Beauty Shop

Production-oriented cosmetics e-commerce built as a Django modular monolith.

## Architecture

Frontend -> API v1 -> domain apps -> services/selectors -> PostgreSQL

Infrastructure is kept in `core`, `api`, and `dj_best`. Business domains remain isolated:
`accounts`, `catalog`, `inventory`, `cart`, `orders`, `payments`, `shipping`, `promotions`, `reviews`.

## Engineering rules

- Views handle HTTP only.
- Selectors own read/query logic.
- Services own business transactions.
- Generic API infrastructure must not contain domain rules.
- Redis is used for cache/queue infrastructure; Celery handles background work.
- Secrets come from environment variables.
- No BodyYar medical domain or medical-specific infrastructure is retained.

## Local setup

1. Copy `.env.example` to `.env`.
2. Install requirements.
3. Run `python manage.py migrate`.
4. Run `python manage.py runserver`.
5. Run Celery with `celery -A dj_best.celery.app worker -l INFO`.

## Docker

`docker compose up --build`
