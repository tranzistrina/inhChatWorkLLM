@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 setup.py %*
) else (
    python setup.py %*
)
if errorlevel 1 pause
