FROM python:3.13.5-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN ENVIRONMENT=local \
    SECRET_KEY=build-only \
    PUSHER_APP_ID=1 \
    PUSHER_KEY=build-key \
    PUSHER_SECRET=build-secret \
    PUSHER_CLUSTER=mt1 \
    python manage.py collectstatic --noinput

CMD ["/bin/sh", "-c", "exec gunicorn ontrac_project.wsgi --timeout 120 --workers 2 --bind 0.0.0.0:${PORT:-8080}"]
