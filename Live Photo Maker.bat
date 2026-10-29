@echo off
rem Live Photo Maker - double-click to open, or drag a video or photo onto this file.
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" goto run

echo First run: setting up Live Photo Maker (needs internet once)...
where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
if not exist ".venv\Scripts\python.exe" goto nopython
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt || goto failed
rem optional: drag and drop onto the window
".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q tkinterdnd2

:run
start "" ".venv\Scripts\pythonw.exe" "%~dp0livephoto.py" %1
exit /b

:nopython
echo.
echo Python 3 was not found.
echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH",
echo then double-click this file again.
pause
exit /b 1

:failed
echo.
echo Installing the requirements failed. Check your internet connection and try again.
rmdir /s /q .venv
pause
exit /b 1
