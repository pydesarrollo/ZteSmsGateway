@echo off
cd /d "%~dp0"
rem Puerto: API_PORT del .env de esta instalacion (si no esta, 8000)
set "API_PORT=8000"
if exist .env for /f "tokens=1,* delims==" %%a in ('findstr /b /c:"API_PORT=" .env') do set "API_PORT=%%b"
if "%API_PORT%"=="" set "API_PORT=8000"
rem En la tarea programada "python" puede no estar en el PATH; en ese caso usar el lanzador "py"
set "PY=python"
where python >nul 2>&1 || set "PY=py"
%PY% -m uvicorn api:app --host 0.0.0.0 --port %API_PORT% >> log.txt 2>&1
