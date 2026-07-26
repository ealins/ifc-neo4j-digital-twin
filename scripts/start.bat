@echo off
setlocal
cd /d "%~dp0\.."
if not exist .env copy /Y .env.example .env >nul
echo Starting Neo4j brain and FastAPI viewer...
docker compose up --build -d
if errorlevel 1 goto :error
powershell -NoProfile -Command "$u='http://localhost:8000/api/status'; for($i=0;$i -lt 90;$i++){try{Invoke-RestMethod $u -TimeoutSec 3|Out-Null; exit 0}catch{Start-Sleep 2}}; exit 1"
if errorlevel 1 goto :logs
start "" http://localhost:8000/viewer
echo Viewer: http://localhost:8000/viewer
echo FastAPI: http://localhost:8000/docs
echo Neo4j: http://localhost:7474
exit /b 0
:logs
echo API did not become ready. Current logs:
docker compose logs --tail=150
exit /b 1
:error
echo Docker Compose failed.
docker compose logs --tail=150
exit /b 1
