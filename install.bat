@echo off
REM ===========================================================================
REM  Brick Foundry - Windows installer
REM
REM  Double-click this file. It will:
REM    1. check Docker is installed and running
REM    2. ask which port you want
REM    3. download everything and start the app
REM
REM  NO API KEY IS NEEDED. Nothing to sign up for, nothing to paste in.
REM
REM  Run this file again any time to change the port or update to the
REM  latest version. It is safe to run repeatedly.
REM ===========================================================================

setlocal enabledelayedexpansion
title Brick Foundry Installer
cd /d "%~dp0"

set "REPO_RAW=https://raw.githubusercontent.com/oliverinhalo/3d-print-lego/HEAD"
set "DEFAULT_PORT=8100"

echo.
echo  ==========================================================
echo    Brick Foundry - LEGO set to printable STL files
echo  ==========================================================
echo.
echo    No API key or account is needed.
echo.

REM ---------------------------------------------------------------------------
REM  1. Docker present?
REM ---------------------------------------------------------------------------
echo  [1/5] Checking Docker...
docker --version >nul 2>&1
if errorlevel 1 goto no_docker

REM  Is the engine actually running? "docker info" fails if it is not.
docker info >nul 2>&1
if errorlevel 1 goto docker_not_running

REM  Compose v2 is built into modern Docker Desktop.
docker compose version >nul 2>&1
if errorlevel 1 goto no_compose

echo        Docker is ready.
echo.

REM ---------------------------------------------------------------------------
REM  2. Which port?
REM ---------------------------------------------------------------------------
echo  [2/5] Choose a port.
echo.
echo        This is the port on this machine you will use to reach the app.
echo        Press Enter to accept the default.
echo.
set "PORT="
set /p "PORT=       Port [%DEFAULT_PORT%]: "
if "!PORT!"=="" set "PORT=%DEFAULT_PORT%"

REM  Reject anything that is not a plain number.
echo !PORT!| findstr /r "^[0-9][0-9]*$" >nul
if errorlevel 1 goto bad_port
if !PORT! LSS 1 goto bad_port
if !PORT! GTR 65535 goto bad_port

echo.
echo        Using port !PORT!.
echo.

REM ---------------------------------------------------------------------------
REM  3. Get the compose file
REM ---------------------------------------------------------------------------
echo  [3/5] Getting configuration...
if exist "docker-compose.yml" goto have_compose

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { Invoke-WebRequest -UseBasicParsing -Uri '%REPO_RAW%/docker-compose.yml' -OutFile 'docker-compose.yml' -TimeoutSec 60; exit 0 } catch { exit 1 }"
if errorlevel 1 goto download_failed
echo        Downloaded docker-compose.yml
goto compose_ready

:have_compose
echo        Using the docker-compose.yml already in this folder.

:compose_ready
REM  The port lives in .env so the compose file is never edited by hand.
> ".env" echo LEGO_PORT=!PORT!
echo        Saved your port to .env
echo.

REM ---------------------------------------------------------------------------
REM  4. Build and start
REM ---------------------------------------------------------------------------
echo  [4/5] Building and starting...
echo.
echo        The first run downloads the source, builds the image, and then
echo        fetches about 150 MB of LEGO data. Expect 5-15 minutes.
echo        Later runs take a couple of seconds.
echo.

docker compose up -d --build
if errorlevel 1 goto start_failed

echo.
echo  [5/5] Waiting for the app to finish setting up...
echo.
echo        It is downloading the LEGO catalogue and parts library now.
echo        You can watch progress in another window with:
echo            docker compose logs -f
echo.

set /a TRIES=0
set /a MAX_TRIES=180

:wait_loop
set /a TRIES+=1
powershell -NoProfile -Command ^
  "try { Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:!PORT!/api/health' -TimeoutSec 5 | Out-Null; exit 0 } catch { exit 1 }" >nul 2>&1
if not errorlevel 1 goto ready
if !TRIES! GEQ !MAX_TRIES! goto slow_start
set /a REMAIN=TRIES*5
echo        still setting up... ^(!REMAIN! seconds^)
timeout /t 5 /nobreak >nul
goto wait_loop

:ready
echo.
echo  ==========================================================
echo    Ready.
echo.
echo    Open:  http://localhost:!PORT!
echo  ==========================================================
echo.
echo    Useful commands, run from this folder:
echo      docker compose logs -f     see what it is doing
echo      docker compose down        stop it
echo      docker compose up -d       start it again
echo.
echo    To change the port later, just run this file again.
echo.
start "" "http://localhost:!PORT!"
goto end

REM ---------------------------------------------------------------------------
REM  Problems
REM ---------------------------------------------------------------------------
:no_docker
echo.
echo   PROBLEM: Docker is not installed.
echo.
echo   Install Docker Desktop, then run this file again:
echo       https://www.docker.com/products/docker-desktop/
echo.
echo   During setup, choose Linux containers ^(the default^).
goto fail

:docker_not_running
echo.
echo   PROBLEM: Docker is installed but not running.
echo.
echo   Start Docker Desktop and wait for it to say "Engine running",
echo   then run this file again.
goto fail

:no_compose
echo.
echo   PROBLEM: your Docker is too old - it has no "docker compose".
echo.
echo   Update Docker Desktop, then run this file again:
echo       https://www.docker.com/products/docker-desktop/
goto fail

:bad_port
echo.
echo   PROBLEM: "!PORT!" is not a valid port.
echo   Enter a number between 1 and 65535, for example 8100.
goto fail

:download_failed
echo.
echo   PROBLEM: could not download the configuration file.
echo   Check this machine can reach github.com, then try again.
goto fail

:start_failed
echo.
echo   PROBLEM: Docker could not build or start the app.
echo.
echo   The error is printed above. The most common causes are:
echo     - Docker Desktop is in Windows container mode.
echo       Right-click the Docker tray icon, Switch to Linux containers.
echo     - Port !PORT! is already used by something else.
echo       Run this file again and pick a different port.
goto fail

:slow_start
echo.
echo   The app is taking longer than 15 minutes to set up.
echo   It is probably still downloading. Check with:
echo       docker compose logs -f
echo.
echo   When it is done, open:  http://localhost:!PORT!
goto end

:fail
echo.
pause
exit /b 1

:end
echo.
pause
exit /b 0
