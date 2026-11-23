@echo off
cd /d "%~dp0"
rem En la tarea programada "python" puede no estar en el PATH; en ese caso usar el lanzador "py"
set "PY=python"
where python >nul 2>&1 || set "PY=py"
%PY% -m uvicorn api:app --host 0.0.0.0 --port 8000 >> log.txt 2>&1
