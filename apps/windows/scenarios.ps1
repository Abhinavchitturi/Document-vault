# Scenarios a person goes through with the Windows app, played on a real Windows PC (CI), with screenshots:
#   1. the installer asks how this PC uses the vault (nothing chosen beforehand, Next waits for an answer);
#   2. "Use the vault on another computer": pairing with a centre computer and using its documents;
#   3. updating from inside the app: "Update When I Quit" (installed on closing, not reopened), then "Restart &
#      Update" (installed, reopened), the answer kept, the downloaded installer cleaned up.
# Run after the app was installed once (ui-test.ps1), from the project folder:
#
#   powershell -File apps/windows/scenarios.ps1 -Setup dist\DocumentVault-Setup.exe -Newer dist\update\DocumentVault-Setup.exe
#                                               -Python C:\...\python.exe [-Out screens-scenarios]
#
# -Newer is an installer that says it is version 99.0.0: the "update" offered from a pretend releases page.
param(
    [Parameter(Mandatory = $true)][string]$Setup,
    [Parameter(Mandatory = $true)][string]$Newer,
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$Out = "screens-scenarios"
)
. "$PSScriptRoot\ui-helpers.ps1"
$AppExe = Join-Path $env:LOCALAPPDATA "Programs\Document Vault\App\DocumentVault.exe"
$ModeFile = Join-Path $env:LOCALAPPDATA "Document Vault\mode.txt"
$Uninstall = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{6E1C0B9A-2F4D-4E63-9B1A-D0C5F1A7B3E2}_is1"
$work = Join-Path ($env:RUNNER_TEMP, $env:TEMP | Where-Object { $_ } | Select-Object -First 1) "scenarios"
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path $work | Out-Null

function Close-App {
    foreach ($p in Get-Process DocumentVault -ErrorAction SilentlyContinue) {
        $w = Window-Of $p.Id "Document Vault" 20
        $w.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close()
        if (-not $p.WaitForExit(30000)) { Stop-Process -Id $p.Id -Force }
    }
}

function Open-App {
    $p = Start-Process $AppExe -PassThru
    $w = Window-Of $p.Id "Document Vault"
    $w.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).SetWindowVisualState(
        [System.Windows.Automation.WindowVisualState]::Maximized)
    return @($p, $w)
}

Close-App
$centre = $null
$feed = $null
try {
    # ---- 1. the installer's question
    Step "installer" {
        $setupProcess = Start-Process $Setup -PassThru
        $win = Top-Window "Setup - Document Vault*" 120
        Find-Like $win "How will this PC use Document Vault?*" 60 | Out-Null
        Shot "installer-question"
        Press (Find-Button $win "Next*")
        $box = Top-Window "Setup" 20  # nothing chosen: it asks for an answer (a box the installer owns)
        Find-Like $box "Choose how this PC*" 10 | Out-Null
        Shot "installer-must-choose"
        Press (Find-Button $box "OK")
        try { Press (Find-Like $win "Use the vault on another computer*" 10 "RadioButton") }
        catch {  # the options drawn as one list: the second line of it
            $list = Find-Like $win "*" 5 "CheckListBox"
            $r = $list.Current.BoundingRectangle
            [Win32.Mouse]::SetCursorPos([int]($r.X + 24), [int]($r.Y + 32)) | Out-Null
            [Win32.Mouse]::mouse_event(0x02, 0, 0, 0, [System.UIntPtr]::Zero); [Win32.Mouse]::mouse_event(0x04, 0, 0, 0, [System.UIntPtr]::Zero)
        }
        Start-Sleep -Seconds 1
        Shot "installer-chose-connect"
        Press (Find-Button $win "Next*")
        Press (Find-Button $win "Next*" 30)  # the desktop shortcut page
        Press (Find-Button $win "*Install*" 30)
        Press (Find-Button $win "*Finish*" 600)  # opens the app
        if (-not $setupProcess.WaitForExit(60000)) { throw "the installer didn't finish" }
        $mode = (Get-Content $ModeFile -ErrorAction SilentlyContinue | Out-String).Trim()
        if ($mode -ne "remote") { throw "the installer saved '$mode', not the answer 'remote'" }
    }

    # ---- 2. connecting to the centre computer
    Step "connect" {
        $centreFolder = Join-Path $work "centre"
        $script:centre = Start-Process $Python -ArgumentList "apps/windows/scenarios/centre.py", "`"$centreFolder`"" -PassThru -NoNewWindow
        Wait-Until { Test-Path (Join-Path $centreFolder "pairing.json") } 600 "the centre computer"
        $pairing = Get-Content (Join-Path $centreFolder "pairing.json") | ConvertFrom-Json
        $running = Get-Process DocumentVault -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($running) {
            $window = Window-Of $running.Id "Document Vault"
            $window.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).SetWindowVisualState(
                [System.Windows.Automation.WindowVisualState]::Maximized)
        } else { $null, $window = Open-App }
        Type-Into (Find $window "AddressBox" 60) $pairing.address
        Type-Into (Find $window "CodeBox") $pairing.code
        Shot "connect-form"
        Press (Find $window "ConnectButton")
        $security = (($pairing.fingerprint.Substring(0, 16).ToUpper()) -split '(.{4})' | Where-Object { $_ }) -join ' '
        Find-Like $window "*$security*" 60 | Out-Null  # the app shows the centre's security code
        Shot "connect-security-code"
        Press (Find-Button $window "It matches")
        Find $window "Nav-dashboard" 120 | Out-Null
        Press (Find $window "Nav-documents")
        Find-Like $window "Car Insurance 2026.txt*" 120 | Out-Null  # the centre's document, seen from this PC
        Start-Sleep -Seconds 2
        Shot "connected-documents"
    }

    # ---- 3. updating from inside the app (a pretend releases page offers 99.0.0)
    $feed = Join-Path $work "feed"
    New-Item -ItemType Directory -Force -Path $feed | Out-Null
    Copy-Item $Newer (Join-Path $feed "DocumentVault-Setup.exe")
    $installer = Get-Item (Join-Path $feed "DocumentVault-Setup.exe")
    $sha = (Get-FileHash $installer.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    ConvertTo-Json -Depth 5 @(@{ tag_name = "v99.0.0"; draft = $false; body = "A pretend update"; assets = @(@{
        name = "DocumentVault-Setup.exe"; size = $installer.Length; digest = "sha256:$sha"
        browser_download_url = "http://127.0.0.1:8770/DocumentVault-Setup.exe" }) }) |
        Set-Content -Encoding utf8 (Join-Path $feed "releases.json")
    $server = Start-Process $Python -ArgumentList "-m", "http.server", "8770", "--bind", "127.0.0.1", "--directory", "`"$feed`"" -PassThru -NoNewWindow
    $env:DOCUMENT_VAULT_UPDATES_URL = "http://127.0.0.1:8770/releases.json"
    Start-Sleep -Seconds 2

    Step "update-when-i-quit" {
        Close-App
        $app, $window = Open-App
        Find $window "Nav-dashboard" 120 | Out-Null
        Press (Find $window "UpdateOnQuitButton" 120)
        Find-Like $window "*will be installed when you close*" 300 | Out-Null
        Shot "update-waiting-for-quit"
        $window.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close()
        Wait-Until { $app.HasExited } 60 "the app closed"
        Wait-Until { (Get-ItemProperty $Uninstall -ErrorAction SilentlyContinue).DisplayVersion -eq "99.0.0" -and
                     -not (Get-Process "DocumentVault-Setup*" -ErrorAction SilentlyContinue) } 300 "the update installed on quitting"
        Start-Sleep -Seconds 5
        if (Get-Process DocumentVault -ErrorAction SilentlyContinue) { throw "the app opened again after 'Update When I Quit'" }
        if ((Get-Content $ModeFile | Out-String).Trim() -ne "remote") { throw "the update changed this PC's answer" }
        $installed = (Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs" -Recurse -Filter "Document Vault*.lnk").Count
        if ($installed -ne 1) { throw "expected one Start menu shortcut after updating, found $installed" }
    }

    Step "restart-and-update" {
        Set-ItemProperty $Uninstall -Name DisplayVersion -Value "0.0.0"  # so the next update is seen being installed
        $app, $window = Open-App
        Find $window "Nav-dashboard" 120 | Out-Null
        Press (Find $window "UpdateButton" 120)
        Wait-Until { $app.HasExited } 300 "the app closed to update"
        Wait-Until { Get-Process DocumentVault -ErrorAction SilentlyContinue } 300 "the app opened again"
        Wait-Until { (Get-ItemProperty $Uninstall).DisplayVersion -eq "99.0.0" } 120 "the update installed"
        $again = Get-Process DocumentVault | Select-Object -First 1
        $window = Window-Of $again.Id "Document Vault"
        Find $window "Nav-dashboard" 120 | Out-Null  # still connected: the answer and the pairing were kept
        Start-Sleep -Seconds 3
        Shot "reopened-after-update"
        Wait-Until { -not (Get-ChildItem $env:TEMP -Filter "DocumentVault-Setup-*.exe" -ErrorAction SilentlyContinue) } 60 `
            "the downloaded installer removed"
    }
    if ($server) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
    if ($script:problems.Count) { throw "Some scenarios didn't work: $($script:problems -join '; ')" }
    Write-Host "Every scenario worked."
}
catch {
    Shot "failed"
    $crash = Join-Path $env:LOCALAPPDATA "Document Vault\crash.log"
    if (Test-Path $crash) { Get-Content $crash }
    throw
}
finally {
    New-Item -ItemType File -Force -Path (Join-Path $work "centre\stop") | Out-Null
    Close-App
    if ($script:centre -and -not $script:centre.HasExited) { $script:centre.WaitForExit(30000) | Out-Null }
}
