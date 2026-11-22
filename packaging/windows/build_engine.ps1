# Build the vault's engine as one folder that DocumentVault-Setup.exe ships, so a person installs nothing else
# (no Git, Node, Python or winget): a private Python with the vault's packages, the engine's code, and Tesseract
# (the scan reader) with English, page-orientation and Hindi. Run on Windows from the project folder (CI does):
#
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build_engine.ps1 [-Out dist\engine]
#
# Every download is pinned and checked against its SHA-256. The AI model isn't included: the app downloads it
# with one click (backend/llm/local_ai.py), and the search model downloads on the first start.
param([string]$Out = "dist\engine")
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"  # Invoke-WebRequest is much faster without its progress bar

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

Write-Host "2. The engine's code"
Copy-Item -Recurse backend (Join-Path $Out "backend")
Get-ChildItem $Out -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
& $python -m compileall -q (Join-Path $Out "backend")  # compiled now, so the first start doesn't have to

Write-Host "3. Tesseract, the scan reader"
Fetch $Pins.Tesseract "$work\tesseract-setup.exe"
7z x "$work\tesseract-setup.exe" "-o$Out\tesseract" -y | Out-Null
Remove-Item -Recurse -Force (Join-Path $Out 'tesseract\$PLUGINSDIR') -ErrorAction SilentlyContinue
if (-not (Test-Path (Join-Path $Out "tesseract\tesseract.exe"))) { throw "tesseract.exe isn't in the unpacked installer" }
Fetch $Pins.Hindi (Join-Path $Out "tesseract\tessdata\hin.traineddata")

Write-Host "4. Check that it all runs"
& $python -c "import backend.api.app, backend.engine.bridge, torch, faiss; print('engine imports ok')"
if ($LASTEXITCODE -ne 0) { throw "the engine doesn't import" }
& (Join-Path $Out "tesseract\tesseract.exe") --list-langs
if ($LASTEXITCODE -ne 0) { throw "tesseract doesn't run" }
$size = (Get-ChildItem $Out -Recurse -File | Measure-Object Length -Sum).Sum / 1MB
Write-Host ("Engine ready in {0}: {1:N0} MB" -f $Out, $size)
