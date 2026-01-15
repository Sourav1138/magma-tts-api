cat << 'EOF' > Dockerfile
FROM python:3.9-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends gcc python3-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy ALL application files (including app.py)
COPY . .

# Create storage directory
RUN mkdir -p static/audio

# Railway sets PORT automatically
ENV PORT=5000
EXPOSE $PORT

# Run the app (pointing to app.py)
CMD gunicorn --workers 3 --bind 0.0.0.0:$PORT app:app
EOF
