@echo off
cd /d "%~dp0"
if /i "%~1"=="--post-update-relaunch" set "WDHT_SKIP_UPDATE_CHECK=1"
call "%~dp0.service\load_settings.bat"
set "LOAD_SETTINGS_EXIT=%ERRORLEVEL%"
if "%LOAD_SETTINGS_EXIT%"=="8" (
    set "WDHT_SKIP_UPDATE_CHECK=1"
    call "%~f0" --post-update-relaunch
    exit /b %ERRORLEVEL%
)
if "%LOAD_SETTINGS_EXIT%"=="9" exit /b 0
if errorlevel 1 (
    pause
    exit /b 1
)
echo CD=%CD%
echo WDHT_PY=%WDHT_PY%
echo PYTHONHOME=%PYTHONHOME%
echo PYTHONPATH=%PYTHONPATH%
"%WDHT_PY%" -c "import sys; print(sys.executable); print(sys.path); import clanker_hydrus_tagger; print(clanker_hydrus_tagger.__file__)"
pause
