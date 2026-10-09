<#
.SYNOPSIS
Builds the NX MCP GUI bridge add-in (NxMcpGuiBridge.dll) against a local NX installation.

.EXAMPLE
pwsh -File nx_gui_bridge\build.ps1 -NxRoot 'C:\Program Files\Siemens\NX2206'

.EXAMPLE
pwsh -File nx_gui_bridge\build.ps1 -NxRoot 'C:\Program Files\Siemens\NX2206' -Sign
#>
param(
    [string]$NxRoot = $env:UGII_BASE_DIR,
    [string]$OutputDirectory = (Join-Path $PSScriptRoot 'build'),
    [switch]$Sign
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

if ($Sign) {
    $resource = Join-Path $NxRoot 'UGOPEN\NXSigningResource.res'
    $signer = Join-Path $NxRoot 'NXBIN\SignDotNet.exe'
    foreach ($required in @($resource, $signer)) {
        if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
            throw "Signing requires $required. Install the NX Open programming tools for this NX release."
        }
    }
}

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$output = Join-Path $OutputDirectory 'NxMcpGuiBridge.dll'
$arguments = @('/nologo', '/target:library', '/optimize+', '/warn:4', "/out:$output")
foreach ($assembly in 'NXOpen.dll', 'NXOpen.UF.dll', 'NXOpenUI.dll', 'NXOpen.Utilities.dll') {
    $arguments += "/reference:$(Join-Path $managed $assembly)"
}
$arguments += '/reference:System.Windows.Forms.dll'
if ($Sign) {
    $arguments += "/resource:$resource,NXSigningResource.res"
}
$arguments += (Join-Path $PSScriptRoot 'NxMcpGuiBridge.cs')

& $compiler @arguments
if ($LASTEXITCODE -ne 0) {
    throw "csc.exe failed with exit code $LASTEXITCODE"
}
if ($Sign) {
    & $signer $output
    if ($LASTEXITCODE -ne 0) {
        throw "SignDotNet.exe failed with exit code $LASTEXITCODE. Check the dotnet_author license."
    }
    & $signer -verify $output
    if ($LASTEXITCODE -ne 0) {
        throw "SignDotNet.exe -verify failed with exit code $LASTEXITCODE. Do not publish this DLL."
    }
}
Write-Output $output
