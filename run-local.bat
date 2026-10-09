@echo off
rem Menjalankan Auto Post di komputer sendiri (Windows): klik dua kali file ini
rem Port bisa diganti: set PORT=8080 lalu jalankan run-local.bat
setlocal
cd /d "%~dp0"
if "%PORT%"=="" set PORT=8000

set PY=
py -3 -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1 && set PY=py -3
if not defined PY python -c "import sys; sys.exit(sys.version_info < (3, 11))" >nul 2>&1 && set PY=python
if not defined PY (
  echo Python 3.11 atau lebih baru belum terpasang. Unduh di https://www.python.org/downloads/
  echo Saat memasang, centang "Add Python to PATH".
  pause
  exit /b 1
)

if not exist .venv (
  echo Membuat virtual environment (.venv^)...
  %PY% -m venv .venv || goto :error
)
echo Memasang/memeriksa dependency...
.venv\Scripts\python -m pip install --disable-pip-version-check -q -r requirements.txt || goto :error

.venv\Scripts\python scripts\setup_env.py %PORT% || goto :error
.venv\Scripts\python -m uvicorn app.main:app --port %PORT%
pause
exit /b 0

:error
echo.
echo Terjadi kesalahan. Baca pesan di atas.
pause
exit /b 1
