FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN addgroup --system cambrify && adduser --system --ingroup cambrify cambrify
WORKDIR /app

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .
RUN DJANGO_SECRET_KEY=build-only DJANGO_DEBUG=True python manage.py collectstatic --noinput \
    && chown -R cambrify:cambrify /app

USER cambrify
EXPOSE 8000
CMD ["gunicorn", "cambrify.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--access-logfile", "-"]
