@echo off
for %%I in ("%~dp0..") do set "WDHT_ROOT=%%~fI\"
set "WDHT_PY=%WDHT_ROOT%venv\Scripts\python.exe"
set "WDHT_NVIDIA=%WDHT_ROOT%venv\Lib\site-packages\nvidia"
set "PYTHONNOUSERSITE=1"
set "PIP_DISABLE_PIP_VERSION_CHECK=1"
set "PATH=%WDHT_ROOT%venv\Scripts;%WDHT_ROOT%.portable\zlib\dll_x64;%WDHT_NVIDIA%\cudnn\bin;%WDHT_NVIDIA%\cufft\bin;%WDHT_NVIDIA%\cublas\bin;%WDHT_NVIDIA%\cuda_nvrtc\bin;%WDHT_NVIDIA%\cuda_runtime\bin;%PATH%"

set "CUDA_PATH="
set "CUDA_HOME="
set "CUDA_PATH_V12_6="
if exist "%WDHT_NVIDIA%\cuda_runtime\bin\cudart64_110.dll" (
    set "CUDA_PATH=%WDHT_NVIDIA%\cuda_runtime"
    set "CUDA_HOME=%WDHT_NVIDIA%\cuda_runtime"
    set "CUDA_PATH_V11_8=%WDHT_NVIDIA%\cuda_runtime\bin"
)

if exist "%WDHT_ROOT%.portable\mode_gpu.txt" (
    set "WDHT_CPU=0"
) else (
    set "WDHT_CPU=1"
)

if not exist "%WDHT_PY%" (
    echo Local runtime is missing.
    echo Run install_cpu.bat or install_gpu.bat first.
    pause
    exit /b 1
)
