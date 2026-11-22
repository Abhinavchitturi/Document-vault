# Build the vault's engine for Windows: a private Python with the vault's packages, and Tesseract (the scan reader)
# with English, page-orientation and Hindi, so a person installs nothing else (no Git, Node, Python or winget). The
# app downloads it only when the computer keeps the vault. The vault's own code isn't in it: that travels with the
# app (copied here into -AppDir\backend), so most updates are just the app.
#
# The engine is named after what goes into it (requirements.txt and this script): DocumentVault-Engine-Windows-<id>.zip.
# When a release already has that file, it is reused (downloaded here for the tests) instead of built again, and an
# app update doesn't download the engine again. The app gets engine.json: where to download it and its SHA-256.
#
#   pwsh packaging\windows\build_engine.ps1 [-Out dist\engine] [-Tag v0.9.1] [-AppDir dist\windows-app]
#
# Every download is pinned and checked against its SHA-256. The AI model isn't included: the app downloads it
# with one click (backend/llm/local_ai.py), and the search model downloads on the first start.
param([string]$Out = "dist\engine", [string]$Tag = "v0.0.0-dev", [string]$AppDir = "")
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is much faster without its progress bar
$Repo = "Abhinavchitturi/Document-vault"

$Pins = @{
    Python = @{
        Url = "https://github.com/astral-sh/python-build-standalone/releases/download/20261026/cpython-3.11.18%2B20261026-x86_64-pc-windows-msvc-install_only.tar.gz"
        Sha = "75cb501dc11c7b55c6b447c58e4910bce5b1f609f1017328b4f3a858dfaf5428"
    }
    Tesseract = @{  # UB-Mannheim's Windows build; its installer is unpacked, not run
        Url = "https://github.com/UB-Mannheim/tesseract/releases/download/v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
        Sha = "c885fff6998e0608ba4bb8ab51436e1c6775c2bafc2559a19b423e18678b60c9"
    }
    Hindi = @{
        Url = "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/87416418657359cb625c412a48b6e1d6d41c29bd/hin.traineddata"
        Sha = "4c73ffc59d497c186b19d1e90f5d721d678ea6b2e277b719bee4e2af12271825"
    }
}

function Fetch($pin, [string]$file) {
    Invoke-WebRequest -UseBasicParsing -Uri $pin.Url -OutFile $file
    $got = (Get-FileHash $file -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($got -ne $pin.Sha) { throw "$file doesn't match its pinned checksum ($got)" }
}

$inputs = [System.Text.Encoding]::UTF8.GetBytes((Get-Content -Raw requirements.txt) + (Get-Content -Raw $PSCommandPath))
$id = [Convert]::ToHexStringLower([System.Security.Cryptography.SHA256]::HashData($inputs)).Substring(0, 12)
$name = "DocumentVault-Engine-Windows-$id.zip"
$zip = Join-Path (Split-Path $Out) $name
New-Item -ItemType Directory -Force -Path (Split-Path $Out) | Out-Null
Remove-Item -Force (Join-Path (Split-Path $Out) "engine-to-upload.txt") -ErrorAction SilentlyContinue

# the same engine already on a release? (then the app's update doesn't download it again)
$published = $null
if (Get-Command gh -ErrorAction SilentlyContinue) {
    $published = gh api "repos/$Repo/releases?per_page=50" --jq ".[] | select(.draft | not) | {tag: .tag_name, a: (.assets[] | select(.name == `"$name`"))} | [.tag, .a.browser_download_url, .a.digest, (.a.size|tostring)] | join(`" `")" 2>$null |
        Select-Object -First 1
}
if ($published) {
    $tagOf, $url, $digest, $bytes = $published -split " "
    Write-Host "Reusing $name from $tagOf"
    gh release download $tagOf --repo $Repo --pattern $name --dir (Split-Path $Out) --clobber
    $sha = $digest -replace "^sha256:", ""
    if ((Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant() -ne $sha) { throw "$name doesn't match its published SHA-256" }
} else {
$work = Join-Path $env:RUNNER_TEMP "engine-build"
if (-not $env:RUNNER_TEMP) { $work = Join-Path $env:TEMP "engine-build" }
Remove-Item -Recurse -Force $Out, $work -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $Out, $work | Out-Null

Write-Host "1. A private Python, with the vault's packages"
Fetch $Pins.Python "$work\python.tar.gz"
tar -xzf "$work\python.tar.gz" -C $Out  # -> $Out\python\python.exe
$python = Join-Path $Out "python\python.exe"
& $python -m pip install --quiet --no-warn-script-location --no-cache-dir -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
& $python -m pip uninstall --quiet -y pytest  # for the project's tests, not the app
# Python's compiled copies (~270 MB): left out of the download, Python makes them again on this PC as it runs
Get-ChildItem $Out -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force

Write-Host "2. Tesseract, the scan reader"
Fetch $Pins.Tesseract "$work\tesseract-setup.exe"
7z x "$work\tesseract-setup.exe" "-o$Out\tesseract" -y | Out-Null
Remove-Item -Recurse -Force (Join-Path $Out 'tesseract\$PLUGINSDIR') -ErrorAction SilentlyContinue
if (-not (Test-Path (Join-Path $Out "tesseract\tesseract.exe"))) { throw "tesseract.exe isn't in the unpacked installer" }
Fetch $Pins.Hindi (Join-Path $Out "tesseract\tessdata\hin.traineddata")

Write-Host "3. Check that it all runs"
$env:PYTHONPATH = (Get-Location).Path  # the vault's code, from this checkout
& $python -c "import backend.api.app, backend.engine.bridge, torch, faiss; print('engine imports ok')"
if ($LASTEXITCODE -ne 0) { throw "the engine doesn't import" }
& (Join-Path $Out "tesseract\tesseract.exe") --list-langs
if ($LASTEXITCODE -ne 0) { throw "tesseract doesn't run" }
Write-Host "4. Leave out what only building software needs (C++ headers, link libraries, test suites, Tk)"
$site = Join-Path $Out "python\Lib\site-packages"
$unused = @("$site\torch\include", "$site\torch\share", "$site\torch\test", "$Out\python\tcl",
            "$Out\python\Lib\tkinter", "$Out\python\Lib\idlelib", "$Out\python\Lib\test", "$Out\python\Lib\turtledemo")
Remove-Item -Recurse -Force $unused -ErrorAction SilentlyContinue
Get-ChildItem $Out -Recurse -File -Include *.lib, *.pdb, *.a | Remove-Item -Force
& $python -c "import backend.api.app, backend.engine.bridge, torch, faiss; print('still imports ok')"
if ($LASTEXITCODE -ne 0) { throw "the engine doesn't import after leaving files out" }
$size = (Get-ChildItem $Out -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Engine ready in {0}: {1:N0} MB" -f $Out, $size)

Write-Host "5. Pack it for the release"
Remove-Item -Force $zip -ErrorAction SilentlyContinue
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::CreateFromDirectory((Resolve-Path $Out).Path, [System.IO.Path]::GetFullPath($zip),
    [System.IO.Compression.CompressionLevel]::SmallestSize, $false)
$sha = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLowerInvariant()
$bytes = (Get-Item $zip).Length
$url = "https://github.com/$Repo/releases/download/$Tag/$name"
Set-Content -Encoding utf8NoBOM (Join-Path (Split-Path $Out) "engine-to-upload.txt") $zip  # new: CI puts it on the release
}
Write-Host ("{0}: {1:N0} MB, sha256 {2}" -f $name, ([long]$bytes / 1MB), $sha)

if ($AppDir) {
    Write-Host "6. The vault's code goes with the app, compiled"
    $code = Join-Path $AppDir "backend"
    Remove-Item -Recurse -Force $code -ErrorAction SilentlyContinue
    Copy-Item -Recurse backend $code
    Get-ChildItem $code -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
    python -m compileall -q $code  # Python 3.11, like the engine's
    @{ version = $id; sha256 = $sha; size = [long]$bytes; url = $url } |
        ConvertTo-Json | Set-Content -Encoding utf8NoBOM (Join-Path $AppDir "engine.json")
}
