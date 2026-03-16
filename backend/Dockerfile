FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Start the application
CMD uvicorn main:app --host 0.0.0.0 --port ${PORT:-8080}
