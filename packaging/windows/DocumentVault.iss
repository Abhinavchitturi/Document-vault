; Document Vault setup for Windows (Inno Setup 6): DocumentVault-Setup.exe.
;   ISCC.exe /DVersion=1.0.0 packaging\windows\DocumentVault.iss   (after: dotnet publish ... -o dist\windows-app)
;
; Installs the native Windows app (WinUI) without administrator rights, then runs install.ps1, which downloads the
; vault's engine from GitHub and sets it up (winget for Git, Python, Node and Tesseract; the AI models). The app
; finds the engine through %USERPROFILE%\.document-vault\install-dir. Uninstalling removes the app and its
; shortcuts; it never deletes the vault folder, so your documents stay where they are.
#ifndef Version
  #define Version "0.0.0-dev"
#endif

[Setup]
AppId={{6E1C0B9A-2F4D-4E63-9B1A-D0C5F1A7B3E2}
AppName=Document Vault
AppVersion={#Version}
AppPublisher=Abhinav Chitturi
AppPublisherURL=https://github.com/Abhinavchitturi/Document-vault
DefaultDirName={localappdata}\Programs\Document Vault
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\..\dist
SetupIconFile=DocumentVault.ico
UninstallDisplayIcon={app}\DocumentVault.ico
OutputBaseFilename=DocumentVault-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=Document Vault
ArchitecturesInstallIn64BitMode=x64

[Tasks]
Name: "remote"; Description: "This PC will connect to the vault on another computer (skips the 6 GB setup)"; Flags: unchecked
Name: "samples"; Description: "Add the fictional sample documents, so there is something to try"; Flags: unchecked
Name: "desktopicon"; Description: "Put a Document Vault shortcut on the desktop"; Flags: unchecked

[Files]
Source: "..\..\dist\windows-app\*"; DestDir: "{app}\App"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\install.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "DocumentVault.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Document Vault"; Filename: "{app}\App\DocumentVault.exe"; WorkingDir: "{app}\App"
Name: "{autodesktop}\Document Vault"; Filename: "{app}\App\DocumentVault.exe"; WorkingDir: "{app}\App"; Tasks: desktopicon

[Run]
; The first install downloads several gigabytes (Python packages, the AI models): the window shows progress.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"" -Dir ""{app}\Document-Vault"" -NoStart -Pause -WithSamples"; \
  StatusMsg: "Setting up Document Vault (this takes a while the first time)..."; Flags: waituntilterminated; Tasks: samples and not remote
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"" -Dir ""{app}\Document-Vault"" -NoStart -Pause"; \
  StatusMsg: "Setting up Document Vault (this takes a while the first time)..."; Flags: waituntilterminated; Tasks: not samples and not remote
Filename: "{app}\App\DocumentVault.exe"; Description: "Open Document Vault"; WorkingDir: "{app}\App"; \
  Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{app}\Document-Vault\vault.cmd"; Parameters: "stop"; RunOnceId: "StopVault"; Flags: runhidden skipifdoesntexist
Filename: "{app}\Document-Vault\vault.cmd"; Parameters: "autostart off"; RunOnceId: "AutostartOff"; Flags: runhidden skipifdoesntexist
