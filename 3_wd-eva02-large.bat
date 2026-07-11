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

set "CPU=%WDHT_CPU%"
set "PRIVACY=1"
if /i "%TAGGER_VERBOSE%"=="1" set "PRIVACY=0"

set "BATCH_INFERENCE_ARG=--batch-inference"
if /i not "%HYDRUS_BATCH_INFERENCE%"=="1" set "BATCH_INFERENCE_ARG=--no-batch-inference"

set "THRESHOLD=%WD_EVA02_LARGE_THRESHOLD%"
set "MAX_TAGS=%WD_EVA02_LARGE_MAX_TAGS%"
set "BATCH_SIZE=%WD_EVA02_LARGE_BATCH_SIZE%"
if not defined BATCH_SIZE set "BATCH_SIZE=16"

set "THRESHOLD_ARG="
if defined THRESHOLD set "THRESHOLD_ARG=--threshold %THRESHOLD%"

set "MAX_TAGS_ARG="
if defined MAX_TAGS set "MAX_TAGS_ARG=--max-tags %MAX_TAGS%"

set "NAMESPACE_ARG="
if defined WD_EVA02_LARGE_NAMESPACE set "NAMESPACE_ARG=--namespace %WD_EVA02_LARGE_NAMESPACE%"

set "SKIP_EXISTING_ARG="
if defined WD_EVA02_LARGE_SKIP_EXISTING_NAMESPACES set "SKIP_EXISTING_ARG=--skip-existing %WD_EVA02_LARGE_SKIP_EXISTING_NAMESPACES%"

"%WDHT_PY%" -m clanker_hydrus_tagger evaluate-api-batch "%HASH_FILE%" --token "%HYDRUS_TOKEN%" --host "%HYDRUS_HOST%" --tag-service "%TAG_SERVICE%" --cpu %CPU% --model "wd-eva02-large-tagger-v3" --privacy %PRIVACY% --batch-size %BATCH_SIZE% %BATCH_INFERENCE_ARG% %THRESHOLD_ARG% %MAX_TAGS_ARG% %NAMESPACE_ARG% %SKIP_EXISTING_ARG%
pause
exit /b %errorlevel%
