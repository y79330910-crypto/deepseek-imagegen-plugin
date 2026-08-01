@echo off
setlocal
set "PORT=8766"
set "URL=http://127.0.0.1:%PORT%"
set "WEBUI=%~dp0plugins\deepseek-imagegen\scripts\webui.py"

rem Already running? Just open the browser.
curl.exe -s -o NUL --max-time 2 "%URL%" >nul 2>&1
if not errorlevel 1 (
  start "" "%URL%"
  exit /b 0
)

rem Start the settings page server (minimized console, close it to stop).
start "DeepSeek ImageGen Settings" /min python "%WEBUI%" --port %PORT%
timeout /t 4 /nobreak >nul
start "" "%URL%"
exit /b 0
