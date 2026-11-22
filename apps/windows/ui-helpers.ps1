# Helpers for clicking through Windows apps like a person would, with UI Automation (what screen readers use):
# find a control by its automation id or name, press it, type into it, and take screenshots. Dot-source it:
#   . "$PSScriptRoot\ui-helpers.ps1"     (set $Out first: where screenshots go)
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Windows.Forms, System.Drawing
$UIA = [System.Windows.Automation.AutomationElement]
$Scope = [System.Windows.Automation.TreeScope]
if (-not $Out) { $Out = "screens" }
New-Item -ItemType Directory -Force -Path $Out | Out-Null
if (-not $script:step) { $script:step = 0 }

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


function Find-Like($within, [string]$pattern, [int]$seconds = 60) {
    # by name, with wildcards (Inno Setup's buttons are "Next >", "&Install"...)
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        $all = $within.FindAll($Scope::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
        foreach ($e in $all) { if ($e.Current.Name -like $pattern) { return $e } }
        Start-Sleep -Milliseconds 500
    }
    Shot "not-found-$($pattern -replace '[^A-Za-z0-9]', '')"
    throw "Couldn't find '$pattern' within $seconds s"
}

function Top-Window([string]$titlePattern, [int]$seconds = 90) {
    # a top-level window by its title (any process: installers, dialogs)
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) {
        foreach ($w in $UIA::RootElement.FindAll($Scope::Children, [System.Windows.Automation.Condition]::TrueCondition)) {
            if ($w.Current.Name -like $titlePattern) { return $w }
        }
        Start-Sleep -Milliseconds 500
    }
    Shot "no-window-$($titlePattern -replace '[^A-Za-z0-9]', '')"
    throw "No window '$titlePattern' within $seconds s"
}

function Wait-Until([scriptblock]$condition, [int]$seconds, [string]$what) {
    $deadline = (Get-Date).AddSeconds($seconds)
    while ((Get-Date) -lt $deadline) { if (& $condition) { return }; Start-Sleep -Milliseconds 500 }
    Shot "timeout-$($what -replace '[^A-Za-z0-9]', '')"
    throw "Waited $seconds s for: $what"
}
