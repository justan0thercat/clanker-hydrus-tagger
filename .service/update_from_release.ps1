param(
    [switch]$CheckOnly,
    [switch]$Quiet,
    [switch]$Force,
    [switch]$SkipPip,
    [switch]$SkipDigestCheck,
    [string]$RepoOwner = "justan0thercat",
    [string]$RepoName = "clanker-hydrus-tagger",
    [string]$AssetName = "clanker-hydrus-tagger-portable.zip"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-RepoRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Get-VersionFromPyproject {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return $null
    }

    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*version\s*=\s*"([^"]+)"\s*$') {
            return $matches[1]
        }
    }

    return $null
}

function Normalize-VersionString {
    param([string]$Value)
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return $null
    }

    $trimmed = $Value.Trim()
    if ($trimmed.StartsWith("refs/tags/")) {
        $trimmed = $trimmed.Substring(10)
    }
    if ($trimmed.StartsWith("v")) {
        $trimmed = $trimmed.Substring(1)
    }
    return $trimmed
}

function ConvertTo-ComparableVersion {
    param([string]$Value)
    $normalized = Normalize-VersionString $Value
    if ([string]::IsNullOrWhiteSpace($normalized)) {
        return $null
    }

    try {
        return [version]$normalized
    }
    catch {
        return $null
    }
}

function Get-ReleaseApiResponse {
    param(
        [string]$Owner,
        [string]$Name
    )

    $headers = @{
        "Accept" = "application/vnd.github+json"
        "User-Agent" = "$Name-updater"
        "X-GitHub-Api-Version" = "2022-11-28"
    }

    $uri = "https://api.github.com/repos/$Owner/$Name/releases/latest"
    try {
        return Invoke-RestMethod -Headers $headers -Uri $uri -TimeoutSec 30
    }
    catch {
        $statusCode = $null
        if ($_.Exception.Response -and $_.Exception.Response.StatusCode) {
            $statusCode = [int]$_.Exception.Response.StatusCode
        }

        if ($statusCode -eq 404) {
            throw "No GitHub release has been published yet for $Owner/$Name. Create a tagged release first."
        }

        throw "Could not reach GitHub Releases for $Owner/$Name. Check your internet connection or GitHub availability. Original error: $($_.Exception.Message)"
    }
}

function Get-ManagedReleaseAsset {
    param(
        $Release,
        [string]$Name
    )

    if (-not $Release.assets) {
        return $null
    }

    foreach ($asset in $Release.assets) {
        if ($asset.name -eq $Name) {
            return $asset
        }
    }

    return $null
}

function Get-AssetSha256 {
    param(
        $Release,
        $Asset
    )

    if ($Asset.digest -and $Asset.digest -match '^sha256:([0-9a-fA-F]{64})$') {
        return $matches[1].ToLowerInvariant()
    }

    if (-not $Release.assets) {
        return $null
    }

    $checksumAssetName = "$($Asset.name).sha256"
    foreach ($candidate in $Release.assets) {
        if ($candidate.name -ne $checksumAssetName) {
            continue
        }

        if (-not $candidate.browser_download_url) {
            continue
        }

        $content = Invoke-WebRequest -Uri $candidate.browser_download_url -UseBasicParsing -TimeoutSec 30
        $text = [string]$content.Content
        if ($text -match '([0-9a-fA-F]{64})') {
            return $matches[1].ToLowerInvariant()
        }
    }

    return $null
}

function Read-Manifest {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path)) {
        return @()
    }

    $lines = Get-Content -LiteralPath $Path
    $entries = @()
    foreach ($line in $lines) {
        $trimmed = $line.Trim()
        if (-not $trimmed) {
            continue
        }
        if ($trimmed.StartsWith("#")) {
            continue
        }
        $entries += $trimmed.Replace("/", "\")
    }
    return $entries
}

function Get-PortableBundleRequiredEntries {
    return @(
        ".service\release_manifest.txt"
        "model\JTP-3\info.json"
        "model\JTP-3\model-labels.csv"
        "model\Z3D-E621-Convnext\info.json"
        "model\wd-eva02-large-tagger-v3\info.json"
        "model\camie-tagger\info.json"
    )
}

function Assert-PortableReleasePayload {
    param([string]$PayloadRoot)

    $manifestPath = Join-Path $PayloadRoot ".service\release_manifest.txt"
    $manifestEntries = Read-Manifest -Path $manifestPath
    $manifestEntrySet = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $manifestEntries) {
        [void]$manifestEntrySet.Add($entry)
    }

    foreach ($requiredEntry in Get-PortableBundleRequiredEntries) {
        if (-not $manifestEntrySet.Contains($requiredEntry)) {
            throw "Release asset is incomplete: manifest is missing required file '$requiredEntry'."
        }

        $candidatePath = Join-Path $PayloadRoot $requiredEntry
        if (-not (Test-Path -LiteralPath $candidatePath)) {
            throw "Release asset is incomplete: packaged file is missing '$requiredEntry'."
        }
    }
}

function Ensure-PathIsInsideRoot {
    param(
        [string]$Root,
        [string]$Candidate
    )

    $fullRoot = [System.IO.Path]::GetFullPath($Root)
    $fullCandidate = [System.IO.Path]::GetFullPath($Candidate)
    if (-not $fullRoot.EndsWith([System.IO.Path]::DirectorySeparatorChar)) {
        $fullRoot += [System.IO.Path]::DirectorySeparatorChar
    }

    if (-not $fullCandidate.StartsWith($fullRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to touch path outside repo root: $fullCandidate"
    }
}

function Remove-ManagedPath {
    param(
        [string]$Root,
        [string]$RelativePath
    )

    $targetPath = Join-Path $Root $RelativePath
    if (-not (Test-Path -LiteralPath $targetPath)) {
        return
    }

    Ensure-PathIsInsideRoot -Root $Root -Candidate $targetPath
    Remove-Item -LiteralPath $targetPath -Force
}

function Copy-ManagedFile {
    param(
        [string]$SourceRoot,
        [string]$DestinationRoot,
        [string]$RelativePath
    )

    $sourcePath = Join-Path $SourceRoot $RelativePath
    $destinationPath = Join-Path $DestinationRoot $RelativePath

    if (-not (Test-Path -LiteralPath $sourcePath)) {
        throw "Release payload is missing managed file: $RelativePath"
    }

    Ensure-PathIsInsideRoot -Root $DestinationRoot -Candidate $destinationPath

    $parent = Split-Path -Parent $destinationPath
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Force
}

function Resolve-ReleasePayloadRoot {
    param([string]$ExtractPath)

    $rootManifest = Join-Path $ExtractPath ".service\release_manifest.txt"
    if (Test-Path -LiteralPath $rootManifest) {
        return $ExtractPath
    }

    $childDirectories = Get-ChildItem -LiteralPath $ExtractPath | Where-Object { $_.PSIsContainer }
    foreach ($directory in $childDirectories) {
        $candidateManifest = Join-Path $directory.FullName ".service\release_manifest.txt"
        if (Test-Path -LiteralPath $candidateManifest) {
            return $directory.FullName
        }
    }

    throw "Could not find an extracted release payload with .service\\release_manifest.txt."
}

function Test-FileSha256 {
    param(
        [string]$Path,
        [string]$ExpectedSha256
    )

    if ([string]::IsNullOrWhiteSpace($ExpectedSha256)) {
        return
    }

    $stream = $null
    $sha256 = $null
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        $actual = ([System.BitConverter]::ToString($sha256.ComputeHash($stream))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        if ($sha256) {
            $sha256.Dispose()
        }
        if ($stream) {
            $stream.Dispose()
        }
    }

    if ($actual -ne $ExpectedSha256.ToLowerInvariant()) {
        throw "Downloaded release asset failed SHA-256 verification."
    }
}

function Invoke-PipRefresh {
    param([string]$Root)

    $pythonExe = Join-Path $Root "venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $pythonExe)) {
        Write-Host "Local venv not found. Skipping dependency refresh."
        Write-Host "Run install_cpu.bat or install_gpu.bat after updating if this is a fresh checkout."
        return
    }

    $requirementsPath = Join-Path $Root "requirements.txt"
    if (Test-Path -LiteralPath (Join-Path $Root ".portable\mode_gpu.txt")) {
        $requirementsPath = Join-Path $Root "requirements-gpu.txt"
    }

    Write-Host "Refreshing dependencies from $(Split-Path -Leaf $requirementsPath)..."
    & $pythonExe -m pip install --upgrade -r $requirementsPath
    if ($LASTEXITCODE -ne 0) {
        throw "pip install failed."
    }

    & $pythonExe -m pip check
    if ($LASTEXITCODE -ne 0) {
        throw "pip check failed."
    }
}

$repoRoot = Get-RepoRoot
$localPyproject = Join-Path $repoRoot "pyproject.toml"
$localVersion = Get-VersionFromPyproject -Path $localPyproject

function Write-Status {
    param([string]$Message)
    if (-not $Quiet) {
        Write-Host $Message
    }
}

try {
    Write-Status "Checking GitHub Releases for $RepoOwner/$RepoName..."
    $release = Get-ReleaseApiResponse -Owner $RepoOwner -Name $RepoName

    $releaseTag = [string]$release.tag_name
    $latestVersion = Normalize-VersionString $releaseTag
    $currentComparable = ConvertTo-ComparableVersion $localVersion
    $latestComparable = ConvertTo-ComparableVersion $latestVersion
    $releaseUrl = [string]$release.html_url
    $asset = Get-ManagedReleaseAsset -Release $release -Name $AssetName

    Write-Status "Current version: $localVersion"
    Write-Status "Latest release:  $latestVersion ($releaseTag)"

    $updateAvailable = $true
    if ($currentComparable -and $latestComparable) {
        $updateAvailable = $latestComparable -gt $currentComparable
    } elseif ($localVersion -and $latestVersion) {
        $updateAvailable = $latestVersion -ne $localVersion
    }

    if (-not $updateAvailable -and -not $Force) {
        Write-Status "Already up to date."
        if ($releaseUrl) {
            Write-Status "Release page: $releaseUrl"
        }
        exit 0
    }

    if ($CheckOnly) {
        if ($updateAvailable) {
            Write-Status "Update available."
            if (-not $asset) {
                Write-Status "Warning: release asset '$AssetName' is missing, so the updater cannot install it yet."
            }
            if ($releaseUrl) {
                Write-Status "Release page: $releaseUrl"
            }
            exit 2
        }

        Write-Status "Already up to date."
        exit 0
    }

    if (-not $asset) {
        throw "Latest release does not include the expected asset '$AssetName'. Publish that asset on the release before updating."
    }

    $assetUrl = [string]$asset.browser_download_url
    if (-not $assetUrl) {
        throw "Release asset '$AssetName' does not expose a browser download URL."
    }

    $tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("clanker-update-" + [System.Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tempRoot -Force | Out-Null

    try {
        $zipPath = Join-Path $tempRoot "release.zip"
        $extractPath = Join-Path $tempRoot "extract"
        $expectedSha256 = $null

        if (-not $SkipDigestCheck) {
            $expectedSha256 = Get-AssetSha256 -Release $release -Asset $asset
            if ($expectedSha256) {
                Write-Host "Using SHA-256: $expectedSha256"
            } else {
                Write-Host "Release asset digest not available. Continuing without SHA-256 verification."
            }
        }

        Write-Host "Downloading release asset $($asset.name)..."
        Invoke-WebRequest -Uri $assetUrl -OutFile $zipPath -UseBasicParsing -TimeoutSec 60

        if ($expectedSha256) {
            Write-Host "Verifying release asset..."
            Test-FileSha256 -Path $zipPath -ExpectedSha256 $expectedSha256
        }

        Write-Host "Extracting release payload..."
        Expand-Archive -LiteralPath $zipPath -DestinationPath $extractPath -Force

        $incomingRoot = Resolve-ReleasePayloadRoot -ExtractPath $extractPath
        Assert-PortableReleasePayload -PayloadRoot $incomingRoot
        $localManifestPath = Join-Path $repoRoot ".service\release_manifest.txt"
        $incomingManifestPath = Join-Path $incomingRoot ".service\release_manifest.txt"

        $currentManifest = Read-Manifest -Path $localManifestPath
        $incomingManifest = Read-Manifest -Path $incomingManifestPath

        if (-not $incomingManifest -or $incomingManifest.Count -eq 0) {
            throw "Incoming release manifest is missing or empty."
        }

        $incomingSet = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
        foreach ($path in $incomingManifest) {
            [void]$incomingSet.Add($path)
        }

        foreach ($path in $currentManifest) {
            if (-not $incomingSet.Contains($path)) {
                Write-Host "Removing retired file: $path"
                Remove-ManagedPath -Root $repoRoot -RelativePath $path
            }
        }

        foreach ($path in $incomingManifest) {
            Write-Host "Updating $path"
            Copy-ManagedFile -SourceRoot $incomingRoot -DestinationRoot $repoRoot -RelativePath $path
        }

        if (-not $SkipPip) {
            Invoke-PipRefresh -Root $repoRoot
        }

        Write-Host ""
        Write-Host "Update complete."
        if ($releaseUrl) {
            Write-Host "Updated from: $releaseUrl"
        }
    }
    finally {
        if (Test-Path -LiteralPath $tempRoot) {
            Remove-Item -LiteralPath $tempRoot -Recurse -Force
        }
    }
}
catch {
    $message = $_.Exception.Message
    if ([string]::IsNullOrWhiteSpace($message)) {
        $message = $_.ToString()
    }
    if ($CheckOnly) {
        Write-Host "Update check failed. Reason: $message"
    } else {
        Write-Host "Update failed. Reason: $message"
    }
    exit 1
}
