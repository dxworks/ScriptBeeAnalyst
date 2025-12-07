FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Data server runs on port 8001 (Supabase Kong uses 8000)
EXPOSE 8001

# Graceful shutdown supported by Uvicorn
CMD ["uvicorn", "src.server:app", "--host", "0.0.0.0", "--port", "8001", "--lifespan", "on"]
