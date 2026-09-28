# NeverRed en Docker. Solo biblioteca estándar de Python, imagen mínima.
# Pinneada por digest (multi-arch): actualízala a propósito, no por sorpresa.
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f

WORKDIR /app
COPY . .

# El servidor lee HOST/PORT/NEVERRED_DB del entorno (ver backend/server.py)
ENV HOST=0.0.0.0 \
    PORT=8000 \
    NEVERRED_DB=/data/neverred.db

RUN useradd -m app && mkdir -p /data && chown app:app /data
USER app

VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')"

CMD ["python3", "backend/server.py"]
