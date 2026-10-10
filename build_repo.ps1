# Builds the Blender extension repository for DanichJPEG add-ons.
#
# Output goes to .\docs - that is the folder GitHub Pages publishes
# (Settings -> Pages -> Deploy from a branch -> main / docs).
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\build_repo.ps1
#
#   -Blender <path>  use a specific blender.exe
#   -Clean           remove old *.zip from docs before building

[CmdletBinding()]
param(
    [string]$Blender = "",
    [string]$Root = "",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

if (-not $Root) {
    $Root = $PSScriptRoot
    if (-not $Root) { $Root = Split-Path -Parent $MyInvocation.MyCommand.Path }
    if (-not $Root) { $Root = (Get-Location).Path }
}

if (-not $Blender) {
    $candidates = @(
        (Join-Path $env:ProgramFiles "Blender Foundation\Blender 5.2\blender.exe"),
        (Join-Path $env:ProgramFiles "Blender Foundation\Blender 5.1\blender.exe"),
        (Join-Path $env:ProgramFiles "Blender Foundation\Blender 5.0\blender.exe")
    )
    $Blender = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $Blender -or -not (Test-Path -LiteralPath $Blender)) {
    throw "blender.exe not found; pass -Blender <path>."
}

$src = Join-Path $Root "src\fflex"
$out = Join-Path $Root "docs"
New-Item -ItemType Directory -Force -Path $out | Out-Null

if ($Clean) {
    Remove-Item -Force (Join-Path $out "*.zip") -ErrorAction SilentlyContinue
    Remove-Item -Force (Join-Path $out "index.json"), (Join-Path $out "index.html") -ErrorAction SilentlyContinue
}

Write-Host "Blender : $Blender"
Write-Host "Source  : $src"
Write-Host "Output  : $out"
Write-Host ""

# 1) Validate the manifest and the package source.
& $Blender --background --factory-startup --command extension validate $src
if ($LASTEXITCODE -ne 0) { throw "manifest validation failed" }

# 2) Build the package: docs\fflex-<version>.zip
& $Blender --background --factory-startup --command extension build --source-dir $src --output-dir $out
if ($LASTEXITCODE -ne 0) { throw "build failed" }

# 3) Generate docs\index.json (+ index.html for browsing in a browser).
#    Only *.zip files in the top level of the folder are listed.
& $Blender --background --factory-startup --command extension server-generate --repo-dir $out --html
if ($LASTEXITCODE -ne 0) { throw "server-generate failed" }

Write-Host ""
Write-Host "Done. Contents of $out :"
Get-ChildItem -LiteralPath $out | Select-Object Name, Length
Write-Host ""
Write-Host "Repository URL for Blender:"
Write-Host "  https://danichjpeg.github.io/Danich_addons/index.json"
Write-Host ""
Write-Host "Local dry run:  python -m http.server 8123 --directory `"$out`""
Write-Host "Local repo URL:  http://127.0.0.1:8123/index.json"
