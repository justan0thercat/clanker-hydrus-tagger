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

set "PRIVACY=1"
if /i "%TAGGER_VERBOSE%"=="1" set "PRIVACY=0"

set "DOUBLECHECK=0"
if /i "%SEARCH_DOUBLECHECK_FILE_SYSTEM%"=="1" set "DOUBLECHECK=1"

"%WDHT_PY%" -m clanker_hydrus_tagger search-all "%~dp0%SEARCH_ALL_FILE%" --token "%HYDRUS_TOKEN%" --host "%HYDRUS_HOST%" --tag-service "%TAG_SERVICE%" --privacy %PRIVACY% --timeout %SEARCH_TIMEOUT% --doublecheck-file-system %DOUBLECHECK% --namespace "%SEARCH_ALL_NAMESPACE%" --sites "%SEARCH_ALL_SITES%"
pause
exit /b %errorlevel%
