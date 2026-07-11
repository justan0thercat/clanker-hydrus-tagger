param(
    [Parameter(Mandatory = $true)]
    [string]$Path
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

foreach ($rawLine in Get-Content -LiteralPath $Path) {
    if ($null -eq $rawLine) {
        continue
    }

    $line = [string]$rawLine
    if ([string]::IsNullOrWhiteSpace($line)) {
        continue
    }

    if ($line -match '^\s*#') {
        continue
    }

    $parts = $line -split '=', 2
    if ($parts.Count -lt 2) {
        continue
    }

    $key = $parts[0].Trim()
    if ([string]::IsNullOrWhiteSpace($key)) {
        continue
    }

    $value = ($parts[1] -replace '\s+#.*$', '').Trim()
    Write-Output ("{0}`t{1}" -f $key, $value)
}
