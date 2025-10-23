# Use a stable base for compatibility with LibreOffice and FFmpeg
FROM ubuntu:22.04

# Set environment variables for non-interactive installation
ENV DEBIAN_FRONTEND=noninteractive

# Install core dependencies: Python, Flask, Gunicorn, FFmpeg, and Headless LibreOffice
# Added dumb-init (better process manager for Gunicorn/Subprocesses)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        python3 python3-pip \
        ffmpeg \
        libreoffice \
        dumb-init \
        libxrender1 \
        fonts-dejavu \
        fonts-noto \
        libfontconfig1 \
        libice6 libsm6 libxtst6 \
    && apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies
RUN pip3 install Flask Flask-CORS gunicorn

# Set the working directory inside the container
WORKDIR /app

# Copy the Python API script
COPY app.py .

# Create the persistent data mount directory (inside container)
VOLUME /tmp/uploads

# Expose the port the Flask app runs on
EXPOSE 8080

# Use dumb-init as the entrypoint
ENTRYPOINT ["/usr/bin/dumb-init", "--"]

# Run the Python server using Gunicorn for production stability
# Configuration: 2 workers, 4 threads, and full logging.
CMD ["gunicorn", "--workers", "2", "--threads", "4", "--bind", "0.0.0.0:8080", "app:app", "--log-level", "info", "--access-logfile", "-", "--error-logfile", "-"]
