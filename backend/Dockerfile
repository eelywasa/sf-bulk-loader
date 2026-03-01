FROM python:3.12-slim

# Run as non-root for security (spec §11)
RUN groupadd -r appuser && useradd -r -g appuser appuser

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Input volume is read-only; output + db volumes are writable
RUN mkdir -p /data/input /data/output /data/db && \
    chown -R appuser:appuser /data

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
