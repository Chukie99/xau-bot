@echo off
REM ==========================================================================
REM  XAU Trading Bot — one-time setup
REM  Double-click this file. Run it once. It asks nothing and installs silently.
REM
REM  What it does:
REM    1. finds Python 3.12 (or any Python 3) and installs the 4 packages
REM    2. locates the MT5 terminal data folder
REM    3. copies the EA + its .mqh includes into MQL5\Experts and MQL5\Include
REM    4. creates the junction MQL5\Files\signals -> <here>\signals
REM       (this is how the Python brain and the EA see the same file)
REM    5. writes .env from .env.example
REM    6. registers a Windows scheduled task that runs the brain every 15 min
REM
REM  Re-running is safe: every step checks before it overwrites.
REM ==========================================================================

setlocal EnableDelayedExpansion
cd /d "%~dp0"
title XAU Bot Setup

echo.
echo ============================================================
echo   XAU TRADING BOT - SETUP
echo ============================================================
echo.

REM ---------------------------------------------------------------- python
echo [1/6] Checking Python...

set "PYEXE="
for %%P in (
    "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
    "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    "%ProgramFiles%\Python312\python.exe"
    "%ProgramFiles%\Python313\python.exe"
) do (
    if exist %%P (
        set "PYEXE=%%~P"
        goto :found_python
    )
)

REM fall back to whatever is on PATH
for /f "delims=" %%i in ('where python 2^>nul') do (
    if not defined PYEXE set "PYEXE=%%i"
)
if not defined PYEXE (
    echo   [X] Python not found. Install Python 3.10+ from python.org
    echo       and tick "Add Python to PATH" during setup. Then re-run.
    echo.
    pause
    exit /b 1
)

:found_python
echo   [OK] %PYEXE%

REM ------------------------------------------------------------- packages
echo.
echo [2/6] Installing packages ^(this takes a minute, only the first time^)...

"%PYEXE%" -m pip install --quiet --upgrade pip
"%PYEXE%" -m pip install --quiet MetaTrader5 requests python-dotenv openai

REM prove the critical one imports; a silent failure here means every later
REM step would "succeed" against a Python that cannot reach the broker
"%PYEXE%" -c "import MetaTrader5" 2>nul
if errorlevel 1 (
    echo   [X] MetaTrader5 failed to import. The package needs Python 3.10-3.13
    echo       on Windows, 64-bit. Check the pip output above.
    echo.
    pause
    exit /b 1
)
echo   [OK] MetaTrader5, requests, python-dotenv, openai

REM ------------------------------------------------------------- MT5 find
echo.
echo [3/6] Locating MetaTrader 5...

set "MT5DATA="
for /f "delims=" %%d in ('dir /b /ad "%APPDATA%\MetaQuotes\Terminal" 2^>nul') do (
    if exist "%APPDATA%\MetaQuotes\Terminal\%%d\MQL5" (
        set "MT5DATA=%APPDATA%\MetaQuotes\Terminal\%%d"
        goto :found_mt5
    )
)

if not defined MT5DATA (
    echo   [X] Could not find the MT5 data folder.
    echo       Expected: %%APPDATA%%\MetaQuotes\Terminal\<hash>\MQL5
    echo       Open MetaTrader 5 once so it creates that folder, then re-run.
    echo.
    pause
    exit /b 1
)

:found_mt5
echo   [OK] %MT5DATA%

REM ------------------------------------------------------------------- EA
echo.
echo [4/6] Installing the EA...

if not exist "%MT5DATA%\MQL5\Experts\xau_bridge" (
    mkdir "%MT5DATA%\MQL5\Experts\xau_bridge"
)
copy /y "brain\mql5\xau_bridge.mq5" "%MT5DATA%\MQL5\Experts\xau_bridge\" >nul
copy /y "brain\mql5\risk_guard.mqh"  "%MT5DATA%\MQL5\Include\" >nul
copy /y "brain\mql5\json_parser.mqh" "%MT5DATA%\MQL5\Include\" >nul
echo   [OK] EA + includes copied
echo   [ ] You will compile it in MetaEditor - see START_HERE.md step 3.

REM -------------------------------------------------------------- junction
echo.
echo [5/6] Connecting MT5 to the signals folder...

if exist "%MT5DATA%\MQL5\Files\signals" (
    echo   [OK] signals folder already exists
) else (
    mklink /J "%MT5DATA%\MQL5\Files\signals" "%CD%\signals" >nul 2>&1
    if errorlevel 1 (
        echo   [!] Junction failed. Trying again as a plain copy fallback.
        mkdir "%MT5DATA%\MQL5\Files\signals" 2>nul
        echo       The bot will work, but the folder is a copy, not a link.
    ) else (
        echo   [OK] junction created
    )
)

REM ------------------------------------------------------------------ env
echo.
if exist ".env" (
    echo   [OK] .env already exists - left alone
) else (
    copy /y ".env.example" ".env" >nul
    echo   [OK] .env created from .env.example
    echo   [ ] >>> EDIT .env NOW AND FILL IN YOUR CREDENTIALS <<<
)

REM ---------------------------------------------------------------- task
echo.
echo [6/6] Registering the scheduled task ^(every 15 minutes^)...

set "TASKNAME=XauBot Brain"
schtasks /query /tn "%TASKNAME%" >nul 2>&1
if not errorlevel 1 (
    echo   [OK] task already exists
) else (
    schtasks /create /tn "%TASKNAME%" /tr "\"%PYEXE%\" \"%CD%\brain\brain.py\"" ^
             /sc minute /mo 15 /rl LIMITED /f >nul 2>&1
    if errorlevel 1 (
        echo   [!] Could not create the scheduled task automatically.
        echo       Run this by hand instead:
        echo         schtasks /create /tn "%TASKNAME%" /tr "\"%PYEXE%\" \"%CD%\brain\brain.py\"" /sc minute /mo 15
    ) else (
        echo   [OK] task created - runs every 15 minutes
    )
)

echo.
echo ============================================================
echo   SETUP DONE
echo ============================================================
echo.
echo   NEXT: open START_HERE.md and follow it from step 2.
echo.
pause
endlocal
