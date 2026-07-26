@echo off
setlocal
cd /d "%~dp0\.."
if not exist .env copy /Y .env.example .env >nul
echo This removes only the Docker volumes owned by project "ifc-brain-generic".
docker compose down -v --remove-orphans
docker compose up --build -d
if errorlevel 1 (
  docker compose logs --tail=180
  exit /b 1
)
echo Brain reset. Run scripts\start.bat or open http://localhost:8000/viewer
