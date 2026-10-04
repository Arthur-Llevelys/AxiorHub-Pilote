FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --requirement /app/requirements.txt
COPY bridge.py app.py /app/
RUN chmod 644 /app/bridge.py /app/app.py /app/requirements.txt
RUN useradd --system --uid 9011 --home /nonexistent --shell /usr/sbin/nologin bridge
USER bridge
EXPOSE 9011
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "9011", "--workers", "1", "--no-access-log", "--no-proxy-headers"]
