# ─── Build: imagen ligera, dependencias pinneadas ──────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencias del sistema solo lo necesario para compilar wheels nativos
# (chardet pure-python, pero algunas wheels piden gcc).
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Instala deps en una capa cacheable
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copia la app
COPY . .

# Genera estáticos (manifest hashed, comprimido vía whitenoise)
# IMPORTANTE: durante el build no hay BD, así que necesitamos un SECRET_KEY
# placeholder que NO se usa en runtime (lo sobrescribe el .env).
RUN SECRET_KEY=build-only-not-used DEBUG=True \
    python manage.py collectstatic --noinput

# Carpetas persistentes — los volúmenes se montan acá
RUN mkdir -p /app/data/mbox /app/data/adjuntos /app/staticfiles

EXPOSE 8000

# Healthcheck Docker-nativo para Coolify
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# Comando: gunicorn con 3 workers (ajusta según RAM del server)
# --max-requests + jitter para reciclar workers (evita memory leaks)
CMD ["python", "-m", "gunicorn", "archivo_pietramonte.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--threads", "2", \
     "--timeout", "60", \
     "--max-requests", "1000", \
     "--max-requests-jitter", "50", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
