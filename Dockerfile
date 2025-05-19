# Use official Python base image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Set working directory
WORKDIR /app

# Copy your script to the container
COPY . .

# Install required packages
RUN pip install --no-cache-dir -r requirements.txt

#directory for persistent stats 
RUN mkdir -p /app/data

EXPOSE 3000

# Run the bot
CMD ["python", "app.py"]
