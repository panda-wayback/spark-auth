FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SPARK_AUTH_DB_PATH=/data/db.sqlite3

WORKDIR /app/server

COPY server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY docs /app/docs
COPY server /app/server

RUN useradd --uid 1000 --create-home app && mkdir /data && chown app /data
USER app
VOLUME /data
EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate --noinput && python manage.py ensure_admin && exec gunicorn config.wsgi -b 0.0.0.0:8000 --workers 2"]
