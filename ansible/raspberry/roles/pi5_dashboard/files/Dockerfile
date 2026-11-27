FROM debian:trixie-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-libgpiod python3-spidev tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pi5_dashboard.py ./
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DASHBOARD_PORT=8080 \
    PIR_HISTORY_DB=/data/motion.sqlite3 DASHBOARD_HOST_SOCKET=/run/pi5-dashboard-host/api.sock
USER 1000:1000
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python3 -c "import json,urllib.request; s=json.load(urllib.request.urlopen('http://127.0.0.1:8080/api/status',timeout=8)); assert s['hardware']['tachometer'] != 'Host helper недоступний'"
CMD ["python3", "/app/pi5_dashboard.py"]
