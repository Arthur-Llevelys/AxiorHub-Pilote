FROM python:3.12-slim
LABEL org.opencontainers.image.title="AxiorHub Pilote" \
      org.opencontainers.image.version="5.6.6" \
      org.opencontainers.image.licenses="AGPL-3.0-or-later" \
      org.opencontainers.image.authors="Timo RAINIO" \
      org.opencontainers.image.description="Production juridique utile, déposée et vérifiée pour cabinets d’avocats"
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-fra libreoffice-writer curl tini minisign && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir waitress==3.0.2 cryptography==44.0.0
RUN useradd --system --uid 10001 --create-home axiorhub
WORKDIR /app
COPY . /app
RUN chmod 0755 /app/docker/entrypoint.sh && mkdir -p /data && chown -R axiorhub:axiorhub /data /app
USER axiorhub
EXPOSE 8626
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 CMD curl -fsS http://127.0.0.1:8626/healthz || exit 1
ENTRYPOINT ["/usr/bin/tini","--","/app/docker/entrypoint.sh"]
