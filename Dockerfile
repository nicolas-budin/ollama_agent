# --- Étape 1 : build du frontend React/Vite ---
FROM node:22-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- Étape 2 : backend FastAPI + frontend buildé ---
FROM python:3.12-slim
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY ollama_client.py web_app.py main.py ./
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

# Ollama tourne sur la machine hôte, pas dans ce conteneur : surcharger
# OLLAMA_URL au run si "host.docker.internal" ne résout pas (Linux sans
# Docker Desktop nécessite --add-host=host.docker.internal:host-gateway).
ENV OLLAMA_URL=http://host.docker.internal:11434/api/chat

EXPOSE 8124
CMD ["uvicorn", "web_app:app", "--host", "0.0.0.0", "--port", "8124"]
