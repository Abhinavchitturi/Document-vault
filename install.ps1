# One-command setup for Document Vault on Windows 10 / 11.
#
#   & ([scriptblock]::Create((irm https://raw.githubusercontent.com/Abhinavchitturi/Document-vault/main/install.ps1))) -WithSamples
#
# or, from a copy you already downloaded:  powershell -ExecutionPolicy Bypass -File .\install.ps1 -WithSamples
#
# Downloads the vault (into %USERPROFILE%\Document-Vault, or -Dir) when it isn't already here, installs what it
# needs with winget (Git, Python, Node, Tesseract, Ollama), sets up the Python packages and the web app, pulls the
# local answer model, starts the vault and opens it in your browser. Safe to run again: it skips what is already
# done and updates the download.
#   -WithSamples   also load the fictional sample documents, so there is something to try
#   -NoStart       set everything up but don't start the vault
#   -SkipModel     don't download the answer model (about 5 GB); answers won't work until you do
#   -Dir <folder>  where the vault goes (default: %USERPROFILE%\Document-Vault)
#   -Pause         wait for a key at the end (used by the installer so its window stays readable)
param(
    [switch]$WithSamples,
    [switch]$NoStart,
    [switch]$SkipModel,
    [switch]$Pause,
    [string]$Dir = ""
)

$ErrorActionPreference = "Stop"
$RepoUrl = if ($env:DOCUMENT_VAULT_REPO) { $env:DOCUMENT_VAULT_REPO } else { "https://github.com/Abhinavchitturi/Document-vault.git" }
$Model = "llama3.1:8b"

function Say($text) { Write-Host ""; Write-Host "==> $text" -ForegroundColor Cyan }

function Stop-Setup($text) {
    Write-Host ""
    Write-Host "Setup stopped: $text" -ForegroundColor Red
    if ($Pause) { Read-Host "Press Enter to close" | Out-Null }
    # run from a file: end with a failure code; run as a pasted command: don't close the person's window
    if ($PSCommandPath) { exit 1 } else { throw "Document Vault setup stopped." }
}

function Update-Path {
    # A program installed a moment ago is on PATH for new windows only; copy the new PATH into this one.
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

function Test-Command($name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Install-Package($id, $label) {
    Say "Installing $label"
    winget install --id $id --exact --silent --accept-package-agreements --accept-source-agreements
    Update-Path
}

function Find-Python {
    # Returns @(program, extra args...) for a working Python 3.11+, or $null. The Microsoft Store's "python"
    # stub exits with an error instead of running, so every candidate is actually run.
    $candidates = @(@("py", "-3.12"), @("py", "-3.11"), @("python"))
    foreach ($c in $candidates) {
        try {
            $exe = $c[0]
            $extra = @()
            if ($c.Length -gt 1) { $extra = @($c[1]) }
            $out = & $exe @extra -c "import sys; print(sys.version_info >= (3, 11))" 2>$null
            if ($LASTEXITCODE -eq 0 -and "$out" -eq "True") { return ,$c }
        } catch { }
    }
    return $null
}

function Find-Tesseract {
    foreach ($p in @("$env:ProgramFiles\Tesseract-OCR\tesseract.exe", "${env:ProgramFiles(x86)}\Tesseract-OCR\tesseract.exe",
                     "$env:LOCALAPPDATA\Programs\Tesseract-OCR\tesseract.exe")) {
        if ($p -and (Test-Path $p)) { return $p }
    }
    $cmd = Get-Command tesseract -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}

function Wait-Ollama($seconds) {
    for ($i = 0; $i -lt $seconds; $i++) {
        try {
            Invoke-WebRequest -Uri "http://localhost:11434/api/tags" -UseBasicParsing -TimeoutSec 2 | Out-Null
            return $true
        } catch { Start-Sleep -Seconds 1 }
    }
    return $false
}

if ($PSVersionTable.PSVersion.Major -lt 5) { Stop-Setup "this needs Windows PowerShell 5 or newer (Windows 10 and 11 have it)." }
if (-not (Test-Command winget)) {
    Stop-Setup "winget is missing. Install 'App Installer' from the Microsoft Store (or update Windows), then run this again."
}

# ---- 1. what the vault needs -------------------------------------------------------------------------------------
if (-not (Test-Command git)) { Install-Package "Git.Git" "Git" }
if (-not (Test-Command git)) { Stop-Setup "Git did not install. Install it from https://git-scm.com, then run this again." }

if (-not (Test-Command node)) { Install-Package "OpenJS.NodeJS.LTS" "Node.js (builds the web pages)" }
if (-not (Test-Command node)) { Stop-Setup "Node.js did not install. Install it from https://nodejs.org, then run this again." }

$python = Find-Python
if (-not $python) {
    Install-Package "Python.Python.3.11" "Python 3.11"
    $python = Find-Python
}
if (-not $python) { Stop-Setup "Python 3.11 or newer did not install. Install it from https://www.python.org, then run this again." }

if (-not (Find-Tesseract)) { Install-Package "UB-Mannheim.TesseractOCR" "Tesseract (reads scans and photos)" }
$tesseract = Find-Tesseract
if (-not $tesseract) { Write-Host "Tesseract is not installed: photos and scanned pages can't be read until it is." -ForegroundColor Yellow }

# ---- 2. the vault itself -----------------------------------------------------------------------------------------
$here = $PSScriptRoot
if ($here -and (Test-Path (Join-Path $here "backend\deploy\vault.py")) -and -not $Dir) {
    $Dir = $here                              # run from a copy that is already downloaded
} elseif ((Test-Path "backend\deploy\vault.py") -and -not $Dir) {
    $Dir = (Get-Location).Path                # run from inside a copy
} else {
    if (-not $Dir) { $Dir = if ($env:DOCUMENT_VAULT_DIR) { $env:DOCUMENT_VAULT_DIR } else { Join-Path $env:USERPROFILE "Document-Vault" } }
    Say "Downloading Document Vault"
    if (Test-Path (Join-Path $Dir ".git")) {
        git -C $Dir pull --ff-only --quiet
        if ($LASTEXITCODE -ne 0) { Write-Host "Could not update $Dir; using the copy that is there." -ForegroundColor Yellow }
    } elseif ((Test-Path $Dir) -and (Get-ChildItem $Dir -Force | Select-Object -First 1)) {
        Stop-Setup "$Dir already exists and is not a Document Vault download. Move it, or pass -Dir with another folder."
    } else {
        git clone --quiet $RepoUrl $Dir
        if ($LASTEXITCODE -ne 0) { Stop-Setup "could not download $RepoUrl. Check your internet connection." }
    }
}
Set-Location $Dir
Write-Host "Using $Dir"

# ---- 3. Python packages and the web app ----------------------------------------------------------------------------
Say "Installing Python packages (the first time this takes several minutes)"
$venvPython = Join-Path $Dir "venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    $exe = $python[0]
    $extra = @()
    if ($python.Length -gt 1) { $extra = @($python[1]) }
    & $exe @extra -m venv venv
    if ($LASTEXITCODE -ne 0) { Stop-Setup "could not create the Python environment." }
}
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -r requirements.txt
if ($LASTEXITCODE -ne 0) { Stop-Setup "installing the Python packages failed. Scroll up for the reason." }

Say "Building the web app"
Push-Location (Join-Path $Dir "frontend")
npm.cmd install --no-audit --no-fund
if ($LASTEXITCODE -ne 0) { Pop-Location; Stop-Setup "npm install failed." }
npm.cmd run build
if ($LASTEXITCODE -ne 0) { Pop-Location; Stop-Setup "building the web app failed." }
Pop-Location

# Hindi for scans and ID cards: Tesseract's installer only brings English, so keep a small language folder in data\.
if ($tesseract) {
    $tessdata = Join-Path $Dir "data\tessdata"
    $source = Join-Path (Split-Path $tesseract) "tessdata"
    if (-not (Test-Path (Join-Path $tessdata "hin.traineddata"))) {
        Say "Adding Hindi to the scan reader"
        try {
            New-Item -ItemType Directory -Force -Path $tessdata | Out-Null
            foreach ($f in @("eng.traineddata", "osd.traineddata")) {
                if (Test-Path (Join-Path $source $f)) { Copy-Item (Join-Path $source $f) $tessdata -Force }
            }
            Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/tesseract-ocr/tessdata_fast/raw/main/hin.traineddata" `
                -OutFile (Join-Path $tessdata "hin.traineddata")
        } catch {
            Write-Host "Could not add Hindi (English still works): $($_.Exception.Message)" -ForegroundColor Yellow
            Remove-Item $tessdata -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

# ---- 4. Ollama (the local AI that writes the answers) ----------------------------------------------------------------
if (-not $SkipModel) {
    Say "Checking Ollama (the local AI that writes the answers)"
    if (-not (Test-Command ollama) -and -not (Test-Path "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe")) {
        Install-Package "Ollama.Ollama" "Ollama"
    }
    $ollama = if (Test-Command ollama) { "ollama" } else { "$env:LOCALAPPDATA\Programs\Ollama\ollama.exe" }
    if (-not (Wait-Ollama 1)) {
        $app = "$env:LOCALAPPDATA\Programs\Ollama\ollama app.exe"
        if (Test-Path $app) { Start-Process $app } else { Start-Process $ollama -ArgumentList "serve" -WindowStyle Hidden }
        if (-not (Wait-Ollama 40)) { Stop-Setup "Ollama is installed but not running. Open the Ollama app, then run this again." }
    }
    $have = & $ollama list
    if (-not ($have | Select-String -SimpleMatch $Model)) {
        Say "Downloading the answer model (about 5 GB, once)"
        & $ollama pull $Model
        if ($LASTEXITCODE -ne 0) { Stop-Setup "downloading the answer model failed. Run this again to retry." }
    }
}

# ---- 5. the search model and the fact reader (so the first start works offline) ----------------------------------------
Say "Downloading the search model and the fact reader (about 500 MB, once)"
& $venvPython -c "from backend.embeddings.embedder import Embedder; Embedder()"
if ($LASTEXITCODE -ne 0) { Write-Host "Could not download the search model now; the vault downloads it on its first start." -ForegroundColor Yellow }
& $venvPython -m backend.extract.needle_setup
if ($LASTEXITCODE -ne 0) { Write-Host "Could not download the fact reader; dates and amounts use the simpler reader for now." -ForegroundColor Yellow }

# ---- 6. samples, start -------------------------------------------------------------------------------------------------
if (-not $env:VAULT_DATABASE_URL) { $env:VAULT_DATABASE_URL = "sqlite:///data/index/vault.db" }
if ($WithSamples) {
    Say "Loading the fictional sample documents"
    & $venvPython -m backend.upload.ingest samples/pdfs samples/scans --owner $env:USERNAME
    if ($LASTEXITCODE -ne 0) { Write-Host "Some samples could not be added (the locked e-Aadhaar needs its password: see the README)." -ForegroundColor Yellow }
}

if (-not $NoStart) {
    Say "Starting the vault"
    & (Join-Path $Dir "vault.cmd") start
    if ($LASTEXITCODE -eq 0) {
        Start-Process "http://localhost:8000"
        Say "All set"
        Write-Host "Document Vault is running at http://localhost:8000 (choose a password on the first screen)."
        Write-Host "Stop it with:  $Dir\vault.cmd stop     Start it again with:  $Dir\vault.cmd start   (or double-click Start Vault.bat)"
    } else {
        Write-Host "The vault did not start; see the messages above." -ForegroundColor Yellow
    }
} else {
    Say "All set"
    Write-Host "Start the vault:  $Dir\vault.cmd start   then open http://localhost:8000 and choose a password."
}
if ($Pause) { Read-Host "Press Enter to close" | Out-Null }
