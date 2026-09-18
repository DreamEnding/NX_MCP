<#
.SYNOPSIS
Builds the NX MCP GUI bridge add-in (NxMcpGuiBridge.dll) against a local NX installation.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File nx_gui_bridge\build.ps1 -NxRoot 'C:\Program Files\Siemens\NX2206'
#>
param(
    [string]$NxRoot = $env:UGII_BASE_DIR,
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'build')
)

$ErrorActionPreference = 'Stop'
if (-not $NxRoot) {
    throw 'Set UGII_BASE_DIR or pass -NxRoot, for example C:\Program Files\Siemens\NX2206.'
}
$managed = Join-Path $NxRoot 'NXBIN\managed'
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
foreach ($required in @($compiler, (Join-Path $managed 'NXOpen.dll'))) {
    if (-not (Test-Path -LiteralPath $required)) {
        throw "Required file not found: $required"
    }
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$output = Join-Path $OutputDirectory 'NxMcpGuiBridge.dll'
$arguments = @('/nologo', '/target:library', '/optimize+', '/warn:4', "/out:$output")
foreach ($assembly in 'NXOpen.dll', 'NXOpen.UF.dll', 'NXOpenUI.dll', 'NXOpen.Utilities.dll') {
    $arguments += "/reference:$(Join-Path $managed $assembly)"
}
$arguments += '/reference:System.Windows.Forms.dll'
$arguments += (Join-Path $PSScriptRoot 'NxMcpGuiBridge.cs')

& $compiler @arguments
if ($LASTEXITCODE -ne 0) {
    throw "csc.exe failed with exit code $LASTEXITCODE"
}
Write-Output $output
