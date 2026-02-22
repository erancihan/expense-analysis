FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if needed (e.g. for pdf libraries)
# apt-get update && apt-get install -y ...

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ src/
COPY data/ data/

# Create a volume for data persistence if not using a bind mount
VOLUME /app/data

# Environment variables
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "src.main"]
