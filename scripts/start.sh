#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || cp .env.example .env
docker compose up --build -d
printf '%s\n' 'Viewer: http://localhost:8000/viewer' 'FastAPI: http://localhost:8000/docs' 'Neo4j: http://localhost:7474'
