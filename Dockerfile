FROM python:3.13-slim

WORKDIR /app

# System dependencies for python packages if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . /app

# Ensure logs directory exists
RUN mkdir -p /app/logs

# Expose FastAPI port
EXPOSE 8000

# Start Uvicorn server
CMD ["uvicorn", "app.api:app", "--host", "0.0.0.0", "--port", "8000"]
