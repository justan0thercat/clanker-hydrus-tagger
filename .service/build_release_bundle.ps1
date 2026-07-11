param(
    [string]$OutputDir,
    [string]$AssetName = "clanker-hydrus-tagger-portable.zip"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-RepoRoot {
    return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

function Get-VersionFromPyproject {
    param([string]$Path)

    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match '^\s*version\s*=\s*"([^"]+)"\s*$') {
            return $matches[1]
        }
    }

    throw "Could not find version in pyproject.toml."
}

function Read-Manifest {
    param([string]$Path)

    $entries = @()
    foreach ($line in Get-Content -LiteralPath $Path) {
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
        throw "Refusing to package path outside repo root: $fullCandidate"
    }
}

function Copy-ManifestFile {
    param(
        [string]$RepoRoot,
        [string]$StageRoot,
        [string]$RelativePath
    )

    $sourcePath = Join-Path $RepoRoot $RelativePath
    $destinationPath = Join-Path $StageRoot $RelativePath

    if (-not (Test-Path -LiteralPath $sourcePath)) {
        throw "Managed file is missing from the repo: $RelativePath"
    }

    Ensure-PathIsInsideRoot -Root $RepoRoot -Candidate $sourcePath

    $parent = Split-Path -Parent $destinationPath
    if ($parent -and -not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    Copy-Item -LiteralPath $sourcePath -Destination $destinationPath -Force
}

function Get-FileSha256 {
    param([string]$Path)

    $stream = $null
    $sha256 = $null
    try {
        $stream = [System.IO.File]::OpenRead($Path)
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        return ([System.BitConverter]::ToString($sha256.ComputeHash($stream))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        if ($sha256) {
            $sha256.Dispose()
        }
        if ($stream) {
            $stream.Dispose()
        }
    }
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

function Get-ZipEntries {
    param([string]$Path)

    Add-Type -AssemblyName System.IO.Compression.FileSystem

    $archive = $null
    try {
        $archive = [System.IO.Compression.ZipFile]::OpenRead($Path)
        return @($archive.Entries | ForEach-Object { $_.FullName.Replace("/", "\") })
    }
    finally {
        if ($archive) {
            $archive.Dispose()
        }
    }
}

function Assert-PortableBundleContents {
    param([string]$Path)

    $entries = Get-ZipEntries -Path $Path
    $entrySet = [System.Collections.Generic.HashSet[string]]::new([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($entry in $entries) {
        [void]$entrySet.Add($entry)
    }

    foreach ($requiredEntry in Get-PortableBundleRequiredEntries) {
        if (-not $entrySet.Contains($requiredEntry)) {
            throw "Portable bundle is missing required file: $requiredEntry"
        }
    }

    $unexpectedOnnx = @($entries | Where-Object { $_ -match '^model\\[^\\]+\\model\.onnx$' })
    if ($unexpectedOnnx.Count -gt 0) {
        $unexpectedList = ($unexpectedOnnx | Sort-Object) -join ", "
        throw "Portable bundle must not include heavyweight ONNX files: $unexpectedList"
    }
}

$repoRoot = Get-RepoRoot
$manifestPath = Join-Path $repoRoot ".service\release_manifest.txt"
$pyprojectPath = Join-Path $repoRoot "pyproject.toml"
$version = Get-VersionFromPyproject -Path $pyprojectPath

if (-not $OutputDir) {
    $OutputDir = Join-Path $repoRoot "release"
}

if (-not (Test-Path -LiteralPath $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
}

$tempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("clanker-release-" + [System.Guid]::NewGuid().ToString("N"))
$stageRoot = Join-Path $tempRoot "clanker-hydrus-tagger"
$assetPath = Join-Path $OutputDir $AssetName
$checksumPath = "$assetPath.sha256"

New-Item -ItemType Directory -Path $stageRoot -Force | Out-Null

try {
    $manifestEntries = Read-Manifest -Path $manifestPath
    if (-not $manifestEntries -or $manifestEntries.Count -eq 0) {
        throw "Release manifest is missing or empty."
    }

    foreach ($entry in $manifestEntries) {
        Copy-ManifestFile -RepoRoot $repoRoot -StageRoot $stageRoot -RelativePath $entry
    }

    if (Test-Path -LiteralPath $assetPath) {
        Remove-Item -LiteralPath $assetPath -Force
    }

    if (Test-Path -LiteralPath $checksumPath) {
        Remove-Item -LiteralPath $checksumPath -Force
    }

    Compress-Archive -Path (Join-Path $stageRoot "*") -DestinationPath $assetPath -CompressionLevel Optimal
    Assert-PortableBundleContents -Path $assetPath

    $hash = Get-FileSha256 -Path $assetPath
    Set-Content -LiteralPath $checksumPath -Value "$hash *$AssetName" -Encoding ascii

    Write-Host "Built release bundle for version $version"
    Write-Host "Zip:      $assetPath"
    Write-Host "Checksum: $checksumPath"
}
finally {
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force
    }
}
