FROM python:3.11-slim-bookworm

# Chromium e chromedriver do Debian: mesma versão entre si e disponíveis para
# arm64 (Mac local) e amd64 (Railway). O link google-chrome permite que o
# Selenium Manager (usado em src/) encontre o navegador sem alterar o código.
RUN apt-get update \
 && apt-get install -y --no-install-recommends chromium chromium-driver fonts-liberation tzdata ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && ln -sf /usr/bin/chromium /usr/bin/google-chrome

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app/src:/app \
    TZ=America/Fortaleza \
    SE_OFFLINE=true \
    DATA_DIR=/data

WORKDIR /app
COPY requirements.txt requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements-web.txt

COPY src ./src
COPY app ./app

# Os scripts de src/ gravam logs e saídas relativos ao diretório corrente;
# rodar a partir de /data mantém tudo no volume persistente.
RUN mkdir -p /data
WORKDIR /data

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.getenv(\"PORT\",\"8000\")}/healthz')"
CMD ["sh", "-c", "exec uvicorn --factory app.main:criar_app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
