# --- build the React frontend ---
FROM node:20-slim AS frontend
WORKDIR /fe
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build   # -> /fe/dist

# --- backend (FastAPI) serving the built SPA ---
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 SCANAPP_FRONTEND_DIR=/app/frontend

WORKDIR /app
COPY backend/requirements.txt .
RUN pip install -r requirements.txt

COPY backend/scanapp ./scanapp
COPY --from=frontend /fe/dist ./frontend

EXPOSE 8080
CMD ["uvicorn", "scanapp.main:app", "--host", "0.0.0.0", "--port", "8080"]
