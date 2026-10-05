FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    PORT=8080 \
    SEAL_DIR=/data/seals

WORKDIR /srv

# 仅标准库，无第三方依赖。
COPY app/ ./app/
COPY tests/ ./tests/
COPY scripts/ ./scripts/

RUN mkdir -p /data/seals && chmod -R 0755 /srv

EXPOSE 8080

HEALTHCHECK --interval=5s --timeout=3s --start-period=3s --retries=3 \
    CMD python -c "import json,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8080/health',timeout=3); assert json.load(r)['status']=='ok'" || exit 1

CMD ["python", "app/server.py"]
