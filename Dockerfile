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

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"

CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi -b 0.0.0.0:8000 --workers 2"]
