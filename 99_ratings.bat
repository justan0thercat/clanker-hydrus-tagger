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

set "RATINGS_MODEL_NAME="
if /i "%RATINGS_MODEL_KEY%"=="WD_EVA02_LARGE" set "RATINGS_MODEL_NAME=wd-eva02-large-tagger-v3"
if /i "%RATINGS_MODEL_KEY%"=="CAMIE_TAGGER" set "RATINGS_MODEL_NAME=camie-tagger"

if not defined RATINGS_MODEL_NAME (
    echo 99_ratings.bat only supports WD_EVA02_LARGE or CAMIE_TAGGER.
    echo Open .env and set RATINGS_MODEL_KEY to one of those values.
    pause
    exit /b 1
)

set "CPU=%WDHT_CPU%"
set "PRIVACY=1"
if /i "%TAGGER_VERBOSE%"=="1" set "PRIVACY=0"

set "BATCH_INFERENCE_ARG=--batch-inference"
if /i not "%HYDRUS_BATCH_INFERENCE%"=="1" set "BATCH_INFERENCE_ARG=--no-batch-inference"

set "THRESHOLD_VAR=%RATINGS_MODEL_KEY%_THRESHOLD"
set "MAX_TAGS_VAR=%RATINGS_MODEL_KEY%_MAX_TAGS"
set "BATCH_SIZE_VAR=%RATINGS_MODEL_KEY%_BATCH_SIZE"

call set "THRESHOLD=%%%THRESHOLD_VAR%%%"
call set "MAX_TAGS=%%%MAX_TAGS_VAR%%%"
call set "BATCH_SIZE=%%%BATCH_SIZE_VAR%%%"
set "NAMESPACE_VAR=%RATINGS_MODEL_KEY%_NAMESPACE"
call set "MODEL_NAMESPACE=%%%NAMESPACE_VAR%%%"
if defined RATINGS_BATCH_SIZE set "BATCH_SIZE=%RATINGS_BATCH_SIZE%"
if not defined BATCH_SIZE set "BATCH_SIZE=4"

set "THRESHOLD_ARG="
if defined THRESHOLD set "THRESHOLD_ARG=--threshold %THRESHOLD%"

set "MAX_TAGS_ARG="
if defined MAX_TAGS set "MAX_TAGS_ARG=--max-tags %MAX_TAGS%"

set "NAMESPACE_ARG="
if defined MODEL_NAMESPACE set "NAMESPACE_ARG=--namespace %MODEL_NAMESPACE%"

set "SKIP_EXISTING_VAR=%RATINGS_MODEL_KEY%_SKIP_EXISTING_NAMESPACES"
call set "MODEL_SKIP_EXISTING=%%%SKIP_EXISTING_VAR%%%"
set "SKIP_EXISTING_ARG="
if defined MODEL_SKIP_EXISTING set "SKIP_EXISTING_ARG=--skip-existing %MODEL_SKIP_EXISTING%"

"%WDHT_PY%" -m clanker_hydrus_tagger evaluate-api-batch "%HASH_FILE%" --token "%HYDRUS_TOKEN%" --host "%HYDRUS_HOST%" --tag-service "%TAG_SERVICE%" --cpu %CPU% --model "%RATINGS_MODEL_NAME%" --privacy %PRIVACY% --batch-size %BATCH_SIZE% %BATCH_INFERENCE_ARG% --ratings-only 1 %THRESHOLD_ARG% %MAX_TAGS_ARG% %NAMESPACE_ARG% %SKIP_EXISTING_ARG%
pause
exit /b %errorlevel%
