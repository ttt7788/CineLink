$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Project = Join-Path $Root "cinelink"
$Fnpack = Join-Path $Root "tools\fnpack.exe"

if (-not (Test-Path $Fnpack)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Fnpack) | Out-Null
    curl.exe --ssl-no-revoke -fL "https://static2.fnnas.com/fnpack/fnpack-1.2.1-windows-amd64" -o $Fnpack
}

& $Fnpack build --directory $Project
