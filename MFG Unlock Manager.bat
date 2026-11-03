@echo off
cd /d "%~dp0"
if exist "%~dp0MFGUnlockManager.exe" (
    start "" "%~dp0MFGUnlockManager.exe"
    exit /b
)
if exist "%~dp0dist\MFGUnlockManager.exe" (
    start "" "%~dp0dist\MFGUnlockManager.exe"
    exit /b
)
where pythonw >nul 2>nul && (start "" pythonw "%~dp0mfgunlocker_web.py" & exit /b)
where py >nul 2>nul && (start "" pyw "%~dp0mfgunlocker_web.py" & exit /b)
start "" python "%~dp0mfgunlocker_web.py"
