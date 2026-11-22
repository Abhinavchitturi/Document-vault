# Click through the Windows app like a person would, with a screenshot of every screen (run by CI on Windows).
#
#   powershell -File apps/windows/ui-test.ps1 -AppPath "$env:LOCALAPPDATA\Programs\Document Vault\App\DocumentVault.exe"
#   powershell -File apps/windows/ui-test.ps1 -AppPath dist\windows-app\DocumentVault.exe -Python C:\...\python.exe
#
# An installed app (DocumentVault-Setup.exe) uses its own engine and its vault in %LOCALAPPDATA%\Document Vault\Vault;
# with -Python, the app runs against this checkout instead (its data\ folder). Either way the vault is preloaded
# with the fictional samples. Uses Windows' UI Automation (what screen readers use) and the automation ids the app
# sets on its controls.
param(
    [Parameter(Mandatory = $true)][string]$AppPath,
    [string]$Python = "",
    [string]$Out = "screens"
)
. "$PSScriptRoot\ui-helpers.ps1"

# ---- a throwaway vault with the fictional samples in it
$env:VAULT_DATABASE_URL = "sqlite:///data/index/vault.db"
$samples = (Get-Location).Path
if ($Python) {
    & $Python -m backend.upload.ingest samples/pdfs samples/scans --owner abhinav
    $env:DOCUMENT_VAULT_DIR = $samples
    $env:DOCUMENT_VAULT_PYTHON = $Python
} else {
    # the installed app downloads its engine when this PC keeps the vault (here from the build itself:
    # $env:DOCUMENT_VAULT_ENGINE_ARCHIVE), then the samples are added with that engine while the app is closed
    $first = Start-Process $AppPath -PassThru
    $window = Window-Of $first.Id "Document Vault"
    $window.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).SetWindowVisualState(
        [System.Windows.Automation.WindowVisualState]::Maximized)
    $here = Find $window "ChooseHere" 60
    Shot "choice"
    Press $here
    Find $window "EngineSetupStatus" 60 | Out-Null
    Start-Sleep -Seconds 2
    Shot "engine-setup"
    Find $window "SubmitButton" 900 | Out-Null  # downloaded, checked, unpacked and started: the first sign-up screen
    $window.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close()  # stops the engine too
    if (-not $first.WaitForExit(30000)) { Stop-Process -Id $first.Id -Force }
    $engine = Join-Path $env:LOCALAPPDATA "Document Vault\Engine"
    Get-Process python -ErrorAction SilentlyContinue | Where-Object { $_.Path -like "$engine*" } | Stop-Process -Force
    $vaultHome = Join-Path $env:LOCALAPPDATA "Document Vault\Vault"
    $env:PYTHONPATH = Split-Path $AppPath  # the vault's code comes with the app; the engine was downloaded
    $env:VAULT_RUNTIME = $engine
    Push-Location $vaultHome
    & (Join-Path $engine "python\python.exe") -m backend.upload.ingest "$samples\samples\pdfs" "$samples\samples\scans" --owner abhinav
    $ingested = $LASTEXITCODE
    Pop-Location
    Remove-Item Env:PYTHONPATH
    if ($ingested -ne 0) { throw "the installed engine couldn't add the samples" }
}
$app = Start-Process $AppPath -PassThru  # (PowerShell names ignore case: the path must not be called $app)
try {
    # 1. first start: choose the admin's password
    $window = Window-Of $app.Id "Document Vault"
    $window.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).SetWindowVisualState(
        [System.Windows.Automation.WindowVisualState]::Maximized)  # the whole app in every screenshot
    if ($Python) {  # 0. first start: this PC as the centre, or connect to another computer (installed: chosen above)
        $here = Find $window "ChooseHere" 60
        Shot "choice"
        Press $here
    }
    $submit = Find $window "SubmitButton" 180
    Shot "setup"
    $password = "ci-only-" + [guid]::NewGuid().ToString("N").Substring(0, 12)  # a throwaway vault's password
    Type-Into (Find $window "NameBox") "abhinav"
    Type-Into (Find $window "PasswordBox") $password
    Type-Into (Find $window "PasswordAgainBox") $password
    Press $submit

    # 2. the dashboard, with the samples
    Find $window "Nav-dashboard" 60 | Out-Null
    Start-Sleep -Seconds 3
    Shot "dashboard"

    # 3. add a document through the real Open dialog
    Step "add" {
        Press (Find $window "Nav-documents")
        Press (Find $window "AddDocumentsButton")
        $dialog = Find $UIA::RootElement "Open" 30  # Windows shows the Open dialog from its own process
        $file = (Resolve-Path "samples/upload-demo/Laptop Warranty.pdf").Path
        Start-Sleep -Seconds 2
        [System.Windows.Forms.SendKeys]::SendWait($file + "{ENTER}")  # the cursor starts in its "File name" box
        Find $window "Added to your vault" 120 | Out-Null
        Shot "added"
        Press (Find $window "OK")
    }

    # 4. the documents list
    Step "documents" {
        Press (Find $window "Nav-documents")
        Start-Sleep -Seconds 2
        Shot "documents"
    }

    # 5. a document in its own window
    Step "document" {
        $list = Find $window "DocumentList"
        $first = $list.FindFirst($Scope::Children, [System.Windows.Automation.Condition]::TrueCondition)
        Press $first
        $document = Window-Of $app.Id ".pdf" 20  # the document's own window: "<name>.pdf - Document Vault"
        Start-Sleep -Seconds 4
        Shot "document"
        $document.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close()
    }

    # 6. ask a question (this throwaway vault has no AI model: the closest passages show instead)
    Step "ask" {
        Press (Find $window "Nav-chat")
        Type-Into (Find $window "QuestionBox") "When does my bike insurance expire?"
        Press (Find $window "SendButton")
        Start-Sleep -Seconds 10
        Shot "ask"
    }

    # 7. settings
    Step "settings" {
        Press (Find $window "Settings")
        Start-Sleep -Seconds 3
        Shot "settings"
    }
    if ($script:problems.Count) { throw "Some screens didn't work: $($script:problems -join '; ')" }
    Write-Host "Clicked through every screen."
}
catch {
    Shot "failed"
    $crash = Join-Path $env:LOCALAPPDATA "Document Vault\crash.log"
    if (Test-Path $crash) { Get-Content $crash }
    throw
}
finally {
    if (-not $app.HasExited) { Stop-Process -Id $app.Id -Force }
}
