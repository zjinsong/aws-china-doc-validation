FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY src ./src

ENV PYTHONPATH=/app/src \
    TRUTHKEEPER_HOST=0.0.0.0 \
    TRUTHKEEPER_PORT=8000 \
    TRUTHKEEPER_DB_PATH=/var/lib/truthkeeper/truthkeeper.db

RUN mkdir -p /var/lib/truthkeeper
EXPOSE 8000
CMD ["python", "-m", "china_doc_truthkeeper.server"]
