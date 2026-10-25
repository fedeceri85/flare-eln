FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV FLARE_MEDIA_ROOT=/media
ENV FLARE_MEDIA_BOUNDARY=/media

WORKDIR /app
RUN mkdir -p /media

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt
COPY . .
ARG FLARE_VERSION=dev
ENV FLARE_VERSION=${FLARE_VERSION}
RUN python manage.py collectstatic --noinput

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
