FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the hospital_api package itself into /app/hospital_api
COPY . /app/hospital_api

# Run uvicorn on the package
CMD ["uvicorn", "hospital_api.main:app", "--host", "0.0.0.0", "--port", "8001"]
