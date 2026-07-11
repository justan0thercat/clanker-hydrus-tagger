@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PY_VERSION=3.11.9"
set "PY_PKG_URL=https://api.nuget.org/v3-flatcontainer/python/3.11.9/python.3.11.9.nupkg"
set "PORTABLE_DIR=%~dp0.portable"
set "PY_DIR=%PORTABLE_DIR%\python-%PY_VERSION%"
set "PY_EXE=%PY_DIR%\tools\python.exe"
set "VENV_PY=%~dp0venv\Scripts\python.exe"

echo.
echo === clanker-hydrus-tagger cpu setup ===
echo root: %~dp0
echo.

if exist "%PY_EXE%" (
    echo [1/7] using portable Python %PY_VERSION% already in .portable.
) else (
    echo [1/7] downloading portable Python %PY_VERSION%...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; New-Item -ItemType Directory -Force $env:PY_DIR | Out-Null; $pkg=Join-Path $env:TEMP 'python.3.11.9.zip'; Invoke-WebRequest -Uri $env:PY_PKG_URL -OutFile $pkg -UseBasicParsing; Expand-Archive -Path $pkg -DestinationPath $env:PY_DIR -Force; Remove-Item $pkg -Force"
    if errorlevel 1 goto fail
)

if exist "%VENV_PY%" (
    echo [2/7] using existing local venv.
) else (
    echo [2/7] creating local Python 3.11 venv...
    "%PY_EXE%" -m venv "%~dp0venv"
    if errorlevel 1 goto fail
)

echo [3/7] checking Python version...
"%VENV_PY%" -c "import sys; print('python', sys.version.split()[0]); raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)"
if errorlevel 1 (
    echo Existing venv is not Python 3.11. Rename or remove the venv folder, then rerun this file.
    goto fail
)

call "%~dp0.service\runtime_env.bat"
if errorlevel 1 goto fail

echo [4/7] updating installer tools...
"%VENV_PY%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto fail

echo [5/7] switching runtime to CPU onnxruntime...
"%VENV_PY%" -m pip uninstall -y onnxruntime-gpu onnxruntime-directml
"%VENV_PY%" -m pip install --upgrade -r "%~dp0requirements.txt"
if errorlevel 1 goto fail

echo [6/7] checking installed packages...
"%VENV_PY%" -m pip check
if errorlevel 1 goto fail

if not exist "%PORTABLE_DIR%" mkdir "%PORTABLE_DIR%"
> "%PORTABLE_DIR%\mode_cpu.txt" echo cpu
if exist "%PORTABLE_DIR%\mode_gpu.txt" del "%PORTABLE_DIR%\mode_gpu.txt"

echo [7/7] smoke test...
"%VENV_PY%" -c "import click, hydrus_api, cv2, pandas, PIL, onnxruntime as ort; print('OK CPU runtime. Providers:', ort.get_available_providers())"
if errorlevel 1 goto fail

echo.
echo CPU install complete.
pause
exit /b 0

:fail
echo.
echo install_cpu.bat failed.
echo scroll up and send the first red/error-looking block if you need help.
pause
exit /b 1
