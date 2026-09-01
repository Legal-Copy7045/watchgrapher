@echo off
REM ===================================================================
REM  WatchGrapher one-button launcher.
REM  Installs Python if it is missing, builds a virtual environment on
REM  first run, then starts the app. Later runs go straight to the app.
REM ===================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0"
title WatchGrapher

REM --- Is this folder writable? --------------------------------------
REM Program Files and most of C:\Windows are protected. The venv, WAV
REM recordings and CSV exports all get written next to this script, so a
REM read-only location cannot work.
echo. > ".__writetest" 2>nul
if not exist ".__writetest" (
    echo.
    echo   Cannot write to:  %CD%
    echo.
    echo   Windows protects this location. Move the whole Timegrapher
    echo   folder somewhere your account owns, for example:
    echo.
    echo       C:\Tools\Timegrapher
    echo.
    echo   then run this file again. Do not just run as administrator --
    echo   the app also writes recordings and exports to this folder.
    echo.
    pause
    exit /b 1
)
del ".__writetest" >nul 2>&1

REM --- Already have a venv? Skip everything else. ---------------------
if exist ".venv\Scripts\python.exe" goto :launch

REM --- Find a Python interpreter --------------------------------------
set "PY="
where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )

if defined PY (
    %PY% -c "import sys; sys.exit(0 if sys.version_info>=(3,10) else 1)" >nul 2>&1
    if errorlevel 1 (
        echo Found Python, but it is older than 3.10. Will install a current one.
        set "PY="
    )
)

if not defined PY goto :installpython
goto :makevenv

REM --- Install Python -------------------------------------------------
:installpython
echo.
echo   Python 3.10+ was not found. Installing it now.
echo   This is a one-time step and needs an internet connection.
echo.

REM Preferred route: winget, present on Windows 10 21H2 and later.
where winget >nul 2>&1
if not errorlevel 1 (
    echo   Installing via winget...
    winget install --id Python.Python.3.12 -e --source winget --accept-package-agreements --accept-source-agreements --disable-interactivity
    call :rehash
    where py >nul 2>&1 && set "PY=py -3"
    if not defined PY ( where python >nul 2>&1 && set "PY=python" )
    if defined PY goto :makevenv
    echo   winget finished but Python is still not on PATH.
)

REM Fallback: download the official installer and run it for this user.
echo   Downloading the official Python installer from python.org...
set "PYURL=https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"
set "PYEXE=%TEMP%\python-watchgrapher-setup.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYURL%' -OutFile '%PYEXE%' -UseBasicParsing; exit 0 } catch { Write-Host $_.Exception.Message; exit 1 }"
if errorlevel 1 (
    echo.
    echo   Could not download Python automatically.
    echo   Install it yourself from https://www.python.org/downloads/
    echo   and tick "Add python.exe to PATH" during setup, then re-run this file.
    echo.
    pause
    exit /b 1
)

echo   Running the installer. This takes a minute and may show a UAC prompt.
"%PYEXE%" /quiet InstallAllUsers=0 PrependPath=1 Include_pip=1 Include_test=0
del "%PYEXE%" >nul 2>&1
call :rehash

where py >nul 2>&1 && set "PY=py -3"
if not defined PY ( where python >nul 2>&1 && set "PY=python" )
if not defined PY (
    echo.
    echo   Python was installed but is not on PATH in this window.
    echo   Close this window and run this file again -- that usually fixes it.
    echo.
    pause
    exit /b 1
)

REM --- Build the virtual environment ----------------------------------
:makevenv
echo.
echo Creating virtual environment with: %PY%
%PY% -m venv .venv
if not exist ".venv\Scripts\python.exe" (
    echo.
    echo   Could not create the virtual environment.
    echo   The error printed above is the real cause.
    echo.
    pause
    exit /b 1
)
echo Installing dependencies. This takes a minute or two...
".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo   Dependency install failed. Try:
    echo     .venv\Scripts\python -m pip install --upgrade "cffi^>=2.0" "PySide6-Essentials^>=6.11"
    echo.
    pause
    exit /b 1
)

:launch
".venv\Scripts\python.exe" -m watchgrapher %*
if errorlevel 1 pause
endlocal
exit /b 0

REM Pull the freshly-installed Python onto PATH for THIS window, since a
REM new install only updates PATH for processes started afterwards.
:rehash
for /f "tokens=2,*" %%A in ('reg query "HKCU\Environment" /v PATH 2^>nul ^| find "PATH"') do set "UPATH=%%B"
for /f "tokens=2,*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH 2^>nul ^| find "PATH"') do set "MPATH=%%B"
set "PATH=%UPATH%;%MPATH%;%PATH%"
exit /b 0
