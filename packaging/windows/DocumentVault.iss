; Document Vault setup for Windows (Inno Setup 6): build with  ISCC.exe /DVersion=1.0.0 packaging\windows\DocumentVault.iss
;
; This is a thin installer. It needs no administrator rights and holds no vault code of its own: it puts
; install.ps1 and the Start Menu shortcuts in place, then runs install.ps1, which downloads the vault from
; GitHub and sets it up (winget for Git, Python, Node, Tesseract and Ollama). Uninstalling removes the
; shortcuts and stops the vault; it never deletes the vault folder, so your documents stay where they are.
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
Name: "samples"; Description: "Add the fictional sample documents, so there is something to try"; Flags: unchecked
Name: "desktopicon"; Description: "Put a Document Vault shortcut on the desktop"; Flags: unchecked

[Files]
Source: "..\..\install.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "DocumentVault.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Document Vault"; Filename: "{app}\Document-Vault\Start Vault.bat"; WorkingDir: "{app}\Document-Vault"; IconFilename: "{app}\DocumentVault.ico"
Name: "{autoprograms}\Stop Document Vault"; Filename: "{app}\Document-Vault\Stop Vault.bat"; WorkingDir: "{app}\Document-Vault"
Name: "{autodesktop}\Document Vault"; Filename: "{app}\Document-Vault\Start Vault.bat"; WorkingDir: "{app}\Document-Vault"; IconFilename: "{app}\DocumentVault.ico"; Tasks: desktopicon

[Run]
; The first install downloads several gigabytes (Python packages, the answer model): the window shows progress.
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"" -Dir ""{app}\Document-Vault"" -NoStart -Pause -WithSamples"; \
  StatusMsg: "Setting up Document Vault (this takes a while the first time)..."; Flags: waituntilterminated; Tasks: samples
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -File ""{app}\install.ps1"" -Dir ""{app}\Document-Vault"" -NoStart -Pause"; \
  StatusMsg: "Setting up Document Vault (this takes a while the first time)..."; Flags: waituntilterminated; Tasks: not samples
Filename: "{app}\Document-Vault\Start Vault.bat"; Description: "Start Document Vault now"; WorkingDir: "{app}\Document-Vault"; \
  Flags: postinstall nowait skipifsilent skipifdoesntexist

[UninstallRun]
Filename: "{app}\Document-Vault\vault.cmd"; Parameters: "stop"; RunOnceId: "StopVault"; Flags: runhidden skipifdoesntexist
Filename: "{app}\Document-Vault\vault.cmd"; Parameters: "autostart off"; RunOnceId: "AutostartOff"; Flags: runhidden skipifdoesntexist
