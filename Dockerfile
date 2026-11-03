# KCU-App – fertiges Image (wird von GitHub Actions gebaut und nach ghcr.io geschoben)
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    KCU_DATA=/data \
    KCU_PHOTOS=/photos \
    TZ=Europe/Berlin \
    HOME=/data/home

WORKDIR /app
COPY app/requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY app/ /app/
RUN mkdir -p /data /photos && chown 99:100 /data

# Unraid-Standard: nobody:users
USER 99:100
EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"

CMD ["gunicorn", "--preload", "--workers", "2", "--threads", "4", "--bind", "0.0.0.0:8000", \
     "--access-logfile", "-", "--forwarded-allow-ips", "*", "kcu:create_app()"]
