<#
    Publish an update that installed copies will pick up.

    An update is the app's own source and nothing else -- about 150 KB, so
    a tester gets a fix in seconds instead of reinstalling 13 MB. The
    private Python and the models are never part of it.

        .\installer\release.ps1 1.0.1 -Notes "Fixed the download bar"

    Needs the GitHub CLI, signed in:  winget install GitHub.cli ; gh auth login
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [string]$Notes = "",
    [switch]$DryRun          # build the files but publish nothing
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$dist = Join-Path $root "dist"

Write-Host "Live Translator release $Version" -ForegroundColor Cyan

# --- the version in the code is the one that ships ------------------------
$initPath = Join-Path $root "livetranslator\__init__.py"
$init = Get-Content $initPath -Raw
if ($init -notmatch '__version__\s*=\s*"([^"]+)"') {
    throw "no __version__ in $initPath"
}
$current = $Matches[1]
if ($current -ne $Version) {
    Write-Host "  version : $current -> $Version"
    ($init -replace '__version__\s*=\s*"[^"]+"', "__version__ = `"$Version`"") |
        Set-Content $initPath -NoNewline -Encoding UTF8
} else {
    Write-Host "  version : $Version (unchanged)"
}

# --- refuse to ship something that does not pass --------------------------
Write-Host "  tests   : running..."
Push-Location $root
try {
    & python -m pytest tests/ -q 2>&1 | Select-Object -Last 1 | Write-Host
    if ($LASTEXITCODE -ne 0) { throw "the tests fail; nothing was published" }
} finally {
    Pop-Location
}

# --- build the update package --------------------------------------------
$tag = "v$Version"
$baseUrl = "https://github.com/OpembaMathews/live-translator/releases/download/$tag"
Push-Location $root
try {
    $build = @"
from livetranslator import update
archive, manifest, body = update.build(version='$Version', base_url='$baseUrl')
print(archive)
print(manifest)
print(f"{body['size']/1e3:.0f} KB  {body['sha256'][:16]}")
"@
    $out = $build | & python -
    if ($LASTEXITCODE -ne 0) { throw "could not build the update package" }
} finally {
    Pop-Location
}
$lines = $out -split "`r?`n" | Where-Object { $_ }
$archive = $lines[0]; $manifestPath = $lines[1]
Write-Host "  package : $($lines[2])"

# notes belong in the manifest, so the dialog can say what changed
if ($Notes) {
    $body = Get-Content $manifestPath -Raw | ConvertFrom-Json
    $body | Add-Member -NotePropertyName notes -NotePropertyValue $Notes -Force
    $body | ConvertTo-Json | Set-Content $manifestPath -Encoding UTF8
}

if ($DryRun) {
    Write-Host ""
    Write-Host "Dry run. Built but not published:" -ForegroundColor Yellow
    Write-Host "  $archive"
    Write-Host "  $manifestPath"
    exit 0
}

# --- publish --------------------------------------------------------------
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Host ""
    Write-Host "The GitHub CLI is not installed. Either:" -ForegroundColor Yellow
    Write-Host "  winget install GitHub.cli ; gh auth login"
    Write-Host "or upload these two files to a release tagged $tag by hand:"
    Write-Host "  $archive"
    Write-Host "  $manifestPath"
    exit 1
}

Push-Location $root
try {
    git add -A
    git commit -m "Release $Version" 2>&1 | Out-Null
    git tag -a $tag -m "Release $Version" 2>&1 | Out-Null
    git push origin HEAD 2>&1 | Out-Null
    git push origin $tag 2>&1 | Out-Null

    # update.json must keep its name: the app asks for it by that name at
    # the releases/latest/download URL, which is what makes "latest" work.
    # No ternary: Windows PowerShell 5.1 is still what "powershell" runs,
    # and it does not have one.
    $releaseNotes = $Notes
    if (-not $releaseNotes) { $releaseNotes = "Update to $Version" }
    & gh release create $tag $archive $manifestPath `
        --title "Live Translator $Version" `
        --notes $releaseNotes
    if ($LASTEXITCODE -ne 0) { throw "gh release create failed" }
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "Published $tag. Installed copies will offer it on their next start." `
    -ForegroundColor Green
