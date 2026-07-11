@echo off

if /i "%WDHT_SETTINGS_LOADED%"=="1" exit /b 0

set "WDHT_RELAUNCH_AFTER_UPDATE_EXIT=8"
set "WDHT_CLOSE_AFTER_UPDATE_EXIT=9"

call "%~dp0runtime_env.bat"
if errorlevel 1 exit /b 1

if not exist "%WDHT_ROOT%.env" (
    echo .env is missing.
    echo Copy .env.example to .env, then paste your Hydrus API key there.
    exit /b 1
)

for /f "usebackq tokens=1* delims=	" %%A in (`powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0print_env_pairs.ps1" "%WDHT_ROOT%.env"`) do (
    if not "%%~A"=="" set "%%~A=%%~B"
)
if errorlevel 1 (
    echo Failed to parse .env
    exit /b 1
)

if not defined HYDRUS_HOST set "HYDRUS_HOST=http://127.0.0.1:45869"
if not defined TAG_SERVICE set "TAG_SERVICE=A.I. Tags"
if not defined HASH_FILE set "HASH_FILE=hashes.txt"
if not defined SEARCH_ARTIST_FILE set "SEARCH_ARTIST_FILE=%HASH_FILE%"
if not defined SEARCH_ALL_FILE set "SEARCH_ALL_FILE=%HASH_FILE%"
if not defined SEARCH_YEAR_FILE set "SEARCH_YEAR_FILE=%HASH_FILE%"
if not defined HYDRUS_BATCH_INFERENCE set "HYDRUS_BATCH_INFERENCE=1"
if not defined TAGGER_VERBOSE set "TAGGER_VERBOSE=0"
if not defined AUTO_CHECK_UPDATES set "AUTO_CHECK_UPDATES=0"
if not defined JTP_3_NAMESPACE set "JTP_3_NAMESPACE=auto"
if not defined Z3D_E621_CONVNEXT_NAMESPACE set "Z3D_E621_CONVNEXT_NAMESPACE=auto"
if not defined WD_EVA02_LARGE_NAMESPACE set "WD_EVA02_LARGE_NAMESPACE=auto"
if not defined CAMIE_TAGGER_NAMESPACE set "CAMIE_TAGGER_NAMESPACE=auto"
if not defined JTP_3_SKIP_EXISTING_NAMESPACES set "JTP_3_SKIP_EXISTING_NAMESPACES="
if not defined Z3D_E621_CONVNEXT_SKIP_EXISTING_NAMESPACES set "Z3D_E621_CONVNEXT_SKIP_EXISTING_NAMESPACES="
if not defined WD_EVA02_LARGE_SKIP_EXISTING_NAMESPACES set "WD_EVA02_LARGE_SKIP_EXISTING_NAMESPACES="
if not defined CAMIE_TAGGER_SKIP_EXISTING_NAMESPACES set "CAMIE_TAGGER_SKIP_EXISTING_NAMESPACES="
if not defined SEARCH_ARTIST_NAMESPACE set "SEARCH_ARTIST_NAMESPACE=creator"
if /i "%SEARCH_ARTIST_NAMESPACE%"=="none" set "SEARCH_ARTIST_NAMESPACE=none"
if /i "%SEARCH_ARTIST_NAMESPACE%"=="off" set "SEARCH_ARTIST_NAMESPACE=none"
if /i "%SEARCH_ARTIST_NAMESPACE%"=="plain" set "SEARCH_ARTIST_NAMESPACE=none"
if /i "%SEARCH_ARTIST_NAMESPACE%"=="raw" set "SEARCH_ARTIST_NAMESPACE=none"
if not defined SEARCH_ALL_NAMESPACE set "SEARCH_ALL_NAMESPACE=general=,copyright=copyright,character=character,meta=meta,species=species,lore=lore,rating=rating,year=year,site=source,filetype=filetype,artist=skip"
if not defined SEARCH_YEAR_NAMESPACE set "SEARCH_YEAR_NAMESPACE=year"
if not defined SEARCH_ARTIST_SITES set "SEARCH_ARTIST_SITES=all"
if not defined SEARCH_ALL_SITES set "SEARCH_ALL_SITES=all"
if not defined SEARCH_YEAR_SITES set "SEARCH_YEAR_SITES=all"
if not defined SEARCH_TIMEOUT set "SEARCH_TIMEOUT=8"
if not defined SOURCE_LOOKUP_MAX_WORKERS set "SOURCE_LOOKUP_MAX_WORKERS=8"
if not defined LOOKUP_RECORD_MAX_WORKERS set "LOOKUP_RECORD_MAX_WORKERS=32"
if not defined SOURCE_RETRY_MAX_ATTEMPTS set "SOURCE_RETRY_MAX_ATTEMPTS=2"
if not defined SOURCE_RETRY_BASE_DELAY_MS set "SOURCE_RETRY_BASE_DELAY_MS=750"
if not defined SOURCE_RETRY_MAX_DELAY_MS set "SOURCE_RETRY_MAX_DELAY_MS=5000"
if not defined SEARCH_DOUBLECHECK_FILE_SYSTEM set "SEARCH_DOUBLECHECK_FILE_SYSTEM=0"
if not defined RATINGS_MODEL_KEY set "RATINGS_MODEL_KEY=WD_EVA02_LARGE"

if not defined HYDRUS_TOKEN (
    echo HYDRUS_TOKEN is empty.
    echo Open .env and paste your key there.
    exit /b 1
)

if /i not "%WDHT_SKIP_UPDATE_CHECK%"=="1" (
    if /i "%AUTO_CHECK_UPDATES%"=="1" (
        echo Checking for updates...
        powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update_from_release.ps1" -CheckOnly
        set "WDHT_UPDATE_CHECK_EXIT=%ERRORLEVEL%"

        if "%WDHT_UPDATE_CHECK_EXIT%"=="2" (
            choice /C UCP /N /M "Update found. [U]pdate and launch, update and [C]lose, [P]roceed without update? "
            set "WDHT_UPDATE_CHOICE=%ERRORLEVEL%"

            if "%WDHT_UPDATE_CHOICE%"=="1" (
                powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update_from_release.ps1"
                if errorlevel 1 exit /b 1
                exit /b %WDHT_RELAUNCH_AFTER_UPDATE_EXIT%
            )

            if "%WDHT_UPDATE_CHOICE%"=="2" (
                powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update_from_release.ps1"
                if errorlevel 1 exit /b 1
                exit /b %WDHT_CLOSE_AFTER_UPDATE_EXIT%
            )
        ) else if not "%WDHT_UPDATE_CHECK_EXIT%"=="0" (
            echo Update check failed. Continuing without blocking this launcher.
        )
    )
)

set "WDHT_SETTINGS_LOADED=1"
exit /b 0
