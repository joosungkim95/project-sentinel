# Stage 1: Build the React dashboard
FROM node:22-alpine AS dashboard-build
WORKDIR /dashboard
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci
COPY dashboard/ ./
RUN npm run build
# Output lands in /dashboard/../api/static → we'll copy from /api/static

# Stage 2: Python app
FROM python:3.12-slim

WORKDIR /app

# Install Python deps
COPY pyproject.toml .
# Bust cache: phase3+4 v2
RUN pip install --no-cache-dir .

# Copy application code (Phase 3+4: learning engine, shadow mode, 8 strategies)
COPY . .

# Copy built dashboard into api/static/
COPY --from=dashboard-build /api/static /app/api/static

# Railway sets PORT dynamically
ENV PORT=8000
EXPOSE ${PORT}

# Health check for Railway
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${PORT}/health')"

CMD ["bash", "scripts/start.sh"]
