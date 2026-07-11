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

"%WDHT_PY%" -m clanker_hydrus_tagger check-api --token "%HYDRUS_TOKEN%" --host "%HYDRUS_HOST%" --tag-service "%TAG_SERVICE%"
pause
