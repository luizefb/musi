@echo off
cd /d "%~dp0"
python -c "import importlib.util,sys; sys.path.insert(0,'.deps'); assert importlib.util.find_spec('mcp') and importlib.util.find_spec('yt_dlp')" >nul 2>&1
if errorlevel 1 (
  echo Instalando componentes do YouTube na pasta local...
  python -m pip install --target .deps -r requirements.txt
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
python app.py
if errorlevel 1 pause
