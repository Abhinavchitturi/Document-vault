# Click through the Windows app like a person would, with a screenshot of every screen (run by CI on Windows).
#
#   powershell -File apps/windows/ui-test.ps1 -AppPath dist\windows-app\DocumentVault.exe -Python C:\...\python.exe -Out screens
#
# The vault is a throwaway one in this checkout's data\ folder, preloaded with the fictional samples. Uses Windows'
# UI Automation (what screen readers use) and the automation ids the app sets on its controls.
param(
    [Parameter(Mandatory = $true)][string]$AppPath,
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$Out = "screens"
)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms, System.Drawing
$UIA = [System.Windows.Automation.AutomationElement]
$Scope = [System.Windows.Automation.TreeScope]
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$script:step = 0

function Shot([string]$name) {
    Start-Sleep -Milliseconds 1200  # let the screen settle
    $script:step++
    $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $bitmap = New-Object System.Drawing.Bitmap $bounds.Width, $bounds.Height
    [System.Drawing.Graphics]::FromImage($bitmap).CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size)
    $file = Join-Path $Out ("{0:D2}-{1}.png" -f $script:step, $name)
    $bitmap.Save($file)
    Write-Host "screenshot: $file"
}

function Find($within, [string]$id, [int]$seconds = 60) {
    # by automation id first, then by name
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        foreach ($property in @($UIA::AutomationIdProperty, $UIA::NameProperty)) {
            $condition = New-Object System.Windows.Automation.PropertyCondition($property, $id)
            $found = $within.FindFirst($Scope::Descendants, $condition)
            if ($found) { return $found }
        }
        Start-Sleep -Milliseconds 500
    }
    Shot "not-found-$($id -replace '[^A-Za-z0-9]', '')"
    throw "Couldn't find '$id' within $seconds s"
}

function Press($element) {
    $pattern = $null
    if ($element.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern, [ref]$pattern)) { $pattern.Invoke(); return }
    if ($element.TryGetCurrentPattern([System.Windows.Automation.SelectionItemPattern]::Pattern, [ref]$pattern)) { $pattern.Select(); return }
    $element.SetFocus(); [System.Windows.Forms.SendKeys]::SendWait(" ")
}

function Type-Into($element, [string]$text) {
    $pattern = $null
    if ($element.TryGetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern, [ref]$pattern)) { $pattern.SetValue($text); return }
    $element.SetFocus(); [System.Windows.Forms.SendKeys]::SendWait($text)
}

function Window-Of([int]$processId, [string]$title = $null, [int]$seconds = 90) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        $condition = New-Object System.Windows.Automation.PropertyCondition($UIA::ProcessIdProperty, $processId)
        foreach ($window in $UIA::RootElement.FindAll($Scope::Children, $condition)) {
            if (-not $title -or $window.Current.Name -like "*$title*") { return $window }
        }
        Start-Sleep -Milliseconds 500
    }
    throw "No window$(if ($title) { " '$title'" }) for the app"
}

$script:problems = @()
function Step([string]$name, [scriptblock]$block) {
    # one screen at a time: a step that fails is photographed and noted, and the next one still runs
    try { & $block }
    catch {
        Write-Warning "${name}: $_"
        $script:problems += "${name}: $_"
        Shot "$name-failed"
        [System.Windows.Forms.SendKeys]::SendWait("{ESC}")
    }
}

# ---- a throwaway vault with the fictional samples in it
$env:VAULT_DATABASE_URL = "sqlite:///data/index/vault.db"
& $Python -m backend.upload.ingest samples/pdfs samples/scans --owner abhinav
$env:DOCUMENT_VAULT_DIR = (Get-Location).Path
$env:DOCUMENT_VAULT_PYTHON = $Python
$app = Start-Process $AppPath -PassThru  # (PowerShell names ignore case: the path must not be called $app)
try {
    # 1. first start: choose the admin's password
    $window = Window-Of $app.Id "Document Vault"
    $window.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).SetWindowVisualState(
        [System.Windows.Automation.WindowVisualState]::Maximized)  # the whole app in every screenshot
    # 0. first start: this PC as the centre, or connect to another computer
    $here = Find $window "ChooseHere" 60
    Shot "choice"
    Press $here
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
