FROM python:3.13.5-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["/bin/sh", "-c", "python manage.py migrate && python manage.py collectstatic --noinput && exec gunicorn ontrac_project.wsgi --timeout 120 --workers 2 --bind 0.0.0.0:${PORT:-8080}"]
