; Document Vault setup for Windows (Inno Setup 6): DocumentVault-Setup.exe.
;   powershell -File packaging\windows\build_engine.ps1                       (-> dist\engine)
;   dotnet publish apps\windows\DocumentVault.App ... -o dist\windows-app
;   ISCC.exe /DVersion=1.0.0 packaging\windows\DocumentVault.iss
;
; Self-contained, like any Windows app: the native app (WinUI) and its engine (a private Python with the vault's
; packages, and Tesseract) go into %LOCALAPPDATA%\Programs\Document Vault, without administrator rights. Nothing
; else is installed (no Git, Node, Python or winget) and nothing is downloaded during setup; the app offers the
; local AI as a one-click download. Your documents live in %LOCALAPPDATA%\Document Vault\Vault: uninstalling or
; updating removes the program files only, never that folder.
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
Name: "desktopicon"; Description: "Put a Document Vault shortcut on the desktop"; Flags: unchecked

[Files]
Source: "..\..\dist\windows-app\*"; DestDir: "{app}\App"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\dist\engine\*"; DestDir: "{app}\Engine"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "DocumentVault.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Document Vault"; Filename: "{app}\App\DocumentVault.exe"; WorkingDir: "{app}\App"
Name: "{autodesktop}\Document Vault"; Filename: "{app}\App\DocumentVault.exe"; WorkingDir: "{app}\App"; Tasks: desktopicon

[Run]
Filename: "{app}\App\DocumentVault.exe"; Description: "Open Document Vault"; WorkingDir: "{app}\App"; \
  Flags: postinstall nowait skipifsilent

[InstallDelete]
; an update replaces the engine whole, so no file of an older version is left behind
Type: filesandordirs; Name: "{app}\Engine"

[UninstallDelete]
; compiled files the engine wrote next to its code
Type: filesandordirs; Name: "{app}\Engine"
