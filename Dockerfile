# ==========================================
# Stage 1: Build React Frontend
# ==========================================
FROM node:20-alpine AS frontend-builder
WORKDIR /app/frontend

# Copy frontend source files
COPY frontend/package*.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# ==========================================
# Stage 2: Build Python Backend & Package
# ==========================================
FROM python:3.11-slim
WORKDIR /app

# Install system dependencies and create non-root user
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home appuser

# Install python dependencies
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY backend/ ./backend

# Copy static frontend assets built in Stage 1 to a directory inside the backend
COPY --from=frontend-builder /app/frontend/dist ./backend/static

# Transfer ownership of the working directory so the non-root user can write
# budgets.json / logging_config.json fallback files at runtime
RUN chown -R appuser:appuser /app

# Set environment defaults
ENV PORT=8000

EXPOSE 8000

# Health check via Python's built-in urllib (curl not available in slim image)
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python3 -c "import urllib.request, os; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('PORT', '8000') + '/healthz')"

USER appuser

# Start server using uvicorn
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT}"]
