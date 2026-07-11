@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "PY_VERSION=3.11.9"
set "PY_PKG_URL=https://api.nuget.org/v3-flatcontainer/python/3.11.9/python.3.11.9.nupkg"
set "ZLIB_URL=https://forums.developer.nvidia.com/uploads/short-url/e76PYqafTHaGM1XQhQumCSL4vqb.zip"
set "PORTABLE_DIR=%~dp0.portable"
set "PY_DIR=%PORTABLE_DIR%\python-%PY_VERSION%"
set "PY_EXE=%PY_DIR%\tools\python.exe"
set "VENV_PY=%~dp0venv\Scripts\python.exe"
set "ZLIB_DIR=%PORTABLE_DIR%\zlib"

echo.
echo === clanker-hydrus-tagger gpu setup ===
echo root: %~dp0
echo.

if exist "%PY_EXE%" (
    echo [1/8] using portable Python %PY_VERSION% already in .portable.
) else (
    echo [1/8] downloading portable Python %PY_VERSION%...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; New-Item -ItemType Directory -Force $env:PY_DIR | Out-Null; $pkg=Join-Path $env:TEMP 'python.3.11.9.zip'; Invoke-WebRequest -Uri $env:PY_PKG_URL -OutFile $pkg -UseBasicParsing; Expand-Archive -Path $pkg -DestinationPath $env:PY_DIR -Force; Remove-Item $pkg -Force"
    if errorlevel 1 goto fail
)

if exist "%VENV_PY%" (
    echo [2/8] using existing local venv.
) else (
    echo [2/8] creating local Python 3.11 venv...
    "%PY_EXE%" -m venv "%~dp0venv"
    if errorlevel 1 goto fail
)

echo [3/8] checking Python version...
"%VENV_PY%" -c "import sys; print('python', sys.version.split()[0]); raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)"
if errorlevel 1 (
    echo Existing venv is not Python 3.11. Rename or remove the venv folder, then rerun this file.
    goto fail
)

echo [4/8] updating installer tools...
"%VENV_PY%" -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto fail

echo [5/8] switching runtime to GPU onnxruntime + local CUDA 11.8/cuDNN 8.9 wheels...
"%VENV_PY%" -m pip uninstall -y onnxruntime onnxruntime-directml
"%VENV_PY%" -m pip install --upgrade -r "%~dp0requirements-gpu.txt"
if errorlevel 1 goto fail

if exist "%ZLIB_DIR%\dll_x64\zlibwapi.dll" (
    echo [6/8] zlibwapi.dll already exists.
) else (
    echo [6/8] downloading zlibwapi.dll...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; New-Item -ItemType Directory -Force $env:ZLIB_DIR | Out-Null; $zip=Join-Path $env:TEMP 'zlib123dllx64.zip'; Invoke-WebRequest -Uri $env:ZLIB_URL -OutFile $zip -UseBasicParsing; Expand-Archive -Path $zip -DestinationPath $env:ZLIB_DIR -Force; Remove-Item $zip -Force"
    if errorlevel 1 goto fail
)

call "%~dp0.service\runtime_env.bat"
if errorlevel 1 goto fail

echo [7/8] checking installed packages...
"%VENV_PY%" -m pip check
if errorlevel 1 goto fail

if not exist "%PORTABLE_DIR%" mkdir "%PORTABLE_DIR%"
> "%PORTABLE_DIR%\mode_gpu.txt" echo gpu
if exist "%PORTABLE_DIR%\mode_cpu.txt" del "%PORTABLE_DIR%\mode_cpu.txt"

echo [8/8] smoke test...
"%VENV_PY%" -c "import click, hydrus_api, cv2, pandas, PIL, onnxruntime as ort; print('OK GPU runtime. Providers:', ort.get_available_providers())"
if errorlevel 1 goto fail

echo.
echo GPU install complete.
echo CUDA/cuDNN/zlib paths are applied locally by .service\runtime_env.bat.
pause
exit /b 0

:fail
echo.
echo install_gpu.bat failed.
echo scroll up and send the first red/error-looking block if you need help.
pause
exit /b 1
