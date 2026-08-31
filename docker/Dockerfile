# syntax=docker/dockerfile:1.7
FROM python:3.13-slim-bookworm
ARG BUILD_REVISION=uncommitted
LABEL org.opencontainers.image.title="Radiology Atlas Viewer" \
      org.opencontainers.image.revision="$BUILD_REVISION" \
      org.opencontainers.image.source="https://github.com/bscongluanbui/radiology-atlas"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=8080
WORKDIR /app
COPY docker/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --disable-pip-version-check -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt \
 && groupadd --gid 10001 atlas && useradd --uid 10001 --gid atlas --no-create-home --shell /usr/sbin/nologin atlas \
 && install -d -o atlas -g atlas -m 0700 /state
COPY --chown=atlas:atlas offline_anatomy_viewer/ /app/offline_anatomy_viewer/
COPY --chown=atlas:atlas anatomy_identity.py overlay_capture.py overlay_capture.js overlay_runtime.js /app/
COPY --chown=atlas:atlas docker/ /app/docker/
USER 10001:10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
 CMD python -c "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2).status==200"
CMD ["gunicorn","--bind=0.0.0.0:8080","--worker-class=gthread","--workers=1","--threads=8","--timeout=120","--graceful-timeout=30","--max-requests=5000","--max-requests-jitter=500","--access-logfile=-","--error-logfile=-","docker.portal:create_app()"]
