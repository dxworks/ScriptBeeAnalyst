FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

# Use --reload only if you want auto-reload inside container
# Graceful shutdown is supported by Uvicorn by default
CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8000", "--lifespan", "on"]
