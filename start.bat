@echo off
REM One-command launcher: .\start  (Windows)
REM Auto-detects the project venv and runs scripts\serve.py.

setlocal
set HERE=%~dp0
if exist "%HERE%.venv\Scripts\python.exe" (
    set PY=%HERE%.venv\Scripts\python.exe
) else (
    set PY=python
)
"%PY%" "%HERE%scripts\serve.py" %*
endlocal
