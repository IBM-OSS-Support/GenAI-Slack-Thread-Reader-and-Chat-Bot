# Dockerfile
FROM python:3.9-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ENV PORT=3000
EXPOSE 3000

CMD ["python", "app.py"]
