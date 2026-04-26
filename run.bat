@echo off
REM Convenience launcher on Windows. Activate venv first or pass full python path via PY env.
if "%PY%"=="" set PY=python
%PY% -m agent_memory_lite %*
