param(
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [Parameter(Mandatory=$true)][string]$BackupRoot,
    [Parameter(Mandatory=$true)][string]$BridgeDescriptor
)
$ErrorActionPreference = 'Stop'
if (Test-Path -LiteralPath $BridgeDescriptor) { throw 'Stop the NX bridge before restoring its runtime.' }
foreach ($required in @('source\src\nx_mcp\__init__.py','venv\Scripts\python.exe')) {
    if (-not (Test-Path (Join-Path $BackupRoot $required))) { throw "Incomplete rollback package: $required" }
}
foreach ($item in @('source','venv','release.json','install.json','validation-release.json')) {
    $source = Join-Path $BackupRoot $item
    $destination = Join-Path $InstallRoot $item
    if (Test-Path $source) {
        if (Test-Path $destination) { Remove-Item -LiteralPath $destination -Recurse -Force }
        Copy-Item -LiteralPath $source -Destination $destination -Recurse
    } elseif ($item -in @('release.json','install.json','validation-release.json') -and (Test-Path $destination)) {
        Remove-Item -LiteralPath $destination -Force
    }
}
Write-Output "Restored runtime from $BackupRoot. Restart the sidecar and restore the saved part manifest."
