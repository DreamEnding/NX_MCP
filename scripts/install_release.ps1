# Run after the NX bridge and its sidecar have stopped. Never stops unrelated NX sessions.
param(
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [Parameter(Mandatory=$true)][string]$BridgeDescriptor,
    [string]$ReleaseRoot = $PSScriptRoot
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $BridgeDescriptor) { throw 'Stop the NX bridge before replacing its runtime.' }
$release = Get-Content (Join-Path $ReleaseRoot 'release.json') -Raw | ConvertFrom-Json
$manifest = Get-Content (Join-Path $ReleaseRoot 'manifest.json') -Raw | ConvertFrom-Json
$prefix = [IO.Path]::GetFullPath($ReleaseRoot).TrimEnd('\') + '\'
foreach ($entry in $manifest.PSObject.Properties) {
    $path = [IO.Path]::GetFullPath((Join-Path $ReleaseRoot $entry.Name))
    if (-not $path.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid manifest path' }
    if ((Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $entry.Value) { throw "Checksum mismatch: $($entry.Name)" }
}
$python = Join-Path $InstallRoot 'venv\Scripts\python.exe'
& $python -c 'import sys; assert sys.version_info[:2] == (3,12), sys.version'
if ($LASTEXITCODE -ne 0) { throw 'This package requires Windows Python 3.12.' }
$backup = Join-Path $InstallRoot ('backups\release-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $backup | Out-Null
foreach ($item in @('source', 'venv', 'release.json', 'install.json', 'validation-release.json')) {
    $path = Join-Path $InstallRoot $item
    if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination (Join-Path $backup $item) -Recurse }
}
Copy-Item (Join-Path $ReleaseRoot 'restore_release.ps1') $backup
try {
    $staged = Join-Path $InstallRoot 'source-release-staging'
    if (Test-Path $staged) { throw 'Resolve previous source staging before installing.' }
    Copy-Item (Join-Path $ReleaseRoot 'source') $staged -Recurse
    & $python -m pip install --no-index --find-links (Join-Path $ReleaseRoot 'wheels') --require-hashes -r (Join-Path $ReleaseRoot 'requirements-windows.lock')
    if ($LASTEXITCODE -ne 0) { throw 'Locked dependency installation failed.' }
    $wheel = Join-Path $ReleaseRoot ('wheels\nx_mcp-' + $release.version + '-py3-none-any.whl')
    & $python -m pip install --no-index --no-deps --force-reinstall $wheel
    if ($LASTEXITCODE -ne 0) { throw 'NX MCP wheel installation failed.' }
    Remove-Item (Join-Path $InstallRoot 'source') -Recurse -Force
    Move-Item $staged (Join-Path $InstallRoot 'source')
    & $python -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Installed dependency validation failed.' }
    Copy-Item (Join-Path $ReleaseRoot 'release.json') (Join-Path $InstallRoot 'release.json') -Force
    # A consolidated package supersedes any earlier validation overlay.
    Remove-Item (Join-Path $InstallRoot 'validation-release.json') -Force -ErrorAction SilentlyContinue
    $installMetadata = Join-Path $InstallRoot 'install.json'
    if (Test-Path $installMetadata) {
        $metadata = Get-Content $installMetadata -Raw | ConvertFrom-Json
        $metadata.PSObject.Properties.Remove('ValidationCommit')
        $metadata | ConvertTo-Json -Depth 10 | Set-Content $installMetadata -Encoding UTF8
    }
    @{version=$release.version;commit=$release.commit;backup=$backup;installed_at=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json
} catch {
    & (Join-Path $backup 'restore_release.ps1') -InstallRoot $InstallRoot -BackupRoot $backup -BridgeDescriptor $BridgeDescriptor
    throw
}
