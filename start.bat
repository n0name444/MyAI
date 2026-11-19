@echo off
setlocal EnableExtensions
cd /d "%~dp0"
title MyAI launcher

rem ===== Settings =====
rem PyTorch CUDA wheel index. If it stops working, take the current one from https://pytorch.org/get-started/locally/
set "TORCH_INDEX=https://download.pytorch.org/whl/cu128"
if "%MYAI_PORT%"=="" (set "PORT=5000") else (set "PORT=%MYAI_PORT%")

echo ==============================================
echo  MyAI launcher
echo ==============================================

set "VENV=%CD%\.venv\Scripts\python.exe"

if exist "%VENV%" goto have_venv

rem ----- find Python 3.10-3.13 -----
set "PYEXE="
set "PYARGS="
where py >nul 2>nul
if not errorlevel 1 (
  for %%V in (3.12 3.11 3.13 3.10) do (
    if not defined PYEXE (
      py -%%V -c "import sys" >nul 2>nul && set "PYEXE=py" && set "PYARGS=-%%V"
    )
  )
)
if not defined PYEXE (
  for /f "delims=" %%P in ('where python 2^>nul') do (
    if not defined PYEXE (
      "%%P" -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,13) else 1)" >nul 2>nul && set "PYEXE=%%P"
    )
  )
)
if not defined PYEXE (
  echo ERROR: Python 3.10-3.13 not found. Install Python 3.12 from python.org and tick Add to PATH.
  goto fail
)
echo Creating virtual environment...
"%PYEXE%" %PYARGS% -m venv .venv
if errorlevel 1 goto fail

:have_venv
"%VENV%" -m pip install --upgrade pip >nul 2>nul

rem ----- PyTorch with CUDA (PyPI wheels for Windows are CPU-only) -----
"%VENV%" check_torch.py
set "TORCH_STATE=%errorlevel%"
if "%TORCH_STATE%"=="0" goto torch_ok
if "%TORCH_STATE%"=="2" goto torch_replace

echo Installing PyTorch with CUDA support (large download, one time)...
"%VENV%" -m pip install torch --index-url "%TORCH_INDEX%"
if errorlevel 1 goto fail
goto torch_ok

:torch_replace
echo Replacing CPU-only PyTorch with the CUDA build...
"%VENV%" -m pip install torch --index-url "%TORCH_INDEX%" --force-reinstall --no-deps
if errorlevel 1 goto fail

:torch_ok

rem ----- other dependencies (reinstalled whenever requirements.txt changes) -----
fc /b requirements.txt ".venv\.myai_requirements.txt" >nul 2>nul
if errorlevel 1 (
  echo Installing MyAI dependencies...
  "%VENV%" -m pip install -r requirements.txt
  if errorlevel 1 goto fail
  copy /y requirements.txt ".venv\.myai_requirements.txt" >nul
)

rem ----- GPU check -----
"%VENV%" check_torch.py --gpu
if errorlevel 1 (
  echo.
  echo WARNING: no CUDA GPU is available to PyTorch. MyAI would run on CPU: slow, small model.
  choice /c YN /m "Continue on CPU anyway"
  if errorlevel 2 goto fail
)

rem ----- start server -----
if exist myai.log del myai.log >nul 2>nul
echo Starting MyAI on port %PORT% ...
start "MyAI Server" /min cmd /c ""%VENV%" app.py >myai.log 2>&1"

where curl >nul 2>nul
if errorlevel 1 (
  timeout /t 8 /nobreak >nul
  goto open_browser
)
set /a TRIES=0
:wait_loop
curl -s -o nul "http://127.0.0.1:%PORT%/health"
if not errorlevel 1 goto open_browser
set /a TRIES+=1
if %TRIES% geq 60 goto start_timeout
timeout /t 1 /nobreak >nul
goto wait_loop

:start_timeout
echo ERROR: the server did not respond. Last lines of myai.log:
if exist myai.log type myai.log
goto fail

:open_browser
start "" "http://127.0.0.1:%PORT%"
echo.
echo MyAI is running: http://127.0.0.1:%PORT%
echo The first start downloads the model (several GB) in the background - watch the status in the sidebar.
echo To stop MyAI, close the "MyAI Server" window in the taskbar.
timeout /t 5 >nul
exit /b 0

:fail
echo.
echo Startup failed. See messages above and myai.log.
pause
exit /b 1
