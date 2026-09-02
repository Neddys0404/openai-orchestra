FROM python:3.11-slim

WORKDIR /app

COPY ai-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY ai-gateway/ .

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]