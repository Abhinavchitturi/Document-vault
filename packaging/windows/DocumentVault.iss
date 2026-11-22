; Document Vault setup for Windows (Inno Setup 6): DocumentVault-Setup.exe.
;   powershell -File packaging\windows\build_engine.ps1                       (-> dist\engine)
;   dotnet publish apps\windows\DocumentVault.App ... -o dist\windows-app
;   ISCC.exe /DVersion=1.0.0 packaging\windows\DocumentVault.iss
;
; Self-contained, like any Windows app: the native app (WinUI) and its engine (a private Python with the vault's
; packages, and Tesseract) go into %LOCALAPPDATA%\Programs\Document Vault, without administrator rights. Nothing
; else is installed (no Git, Node, Python or winget) and nothing is downloaded during setup; the app offers the
; local AI as a one-click download. Your documents live in %LOCALAPPDATA%\Document Vault\Vault: uninstalling or
; updating removes the program files only, never that folder. Its first page asks whether this PC keeps the vault or
; only connects to the vault on another computer.
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

[Code]
// First question of the setup: is this PC the centre (the vault and its documents live here), or does it only use
// the vault on another computer? Saved as the app's own first-start choice (%LOCALAPPDATA%\Document Vault\mode.txt),
// so the app opens straight to the right screen; it can be changed later in the app's Settings. An update keeps the
// earlier choice and doesn't ask again. A silent install asks nothing (the app asks on its first start), unless it
// is given /MODE=here or /MODE=remote.
var
  RolePage: TInputOptionWizardPage;

function ModeFile(): String;
begin
  Result := ExpandConstant('{localappdata}\Document Vault\mode.txt');
end;

procedure InitializeWizard();
begin
  RolePage := CreateInputOptionPage(wpWelcome,
    'How will this PC use Document Vault?',
    'You can change this later in the app''s Settings.',
    'Keep the vault on this PC if your documents and the private AI should live here; other laptops and ' +
    'phones in the family can then connect to it. Choose the other option if this PC should only use the ' +
    'family''s vault on another computer (the centre): nothing is stored here and no AI download is needed.',
    True, False);
  RolePage.Add('Keep the vault on this PC (this PC is the centre)');
  RolePage.Add('Use the vault on another computer (just connect to it)');
  RolePage.Values[0] := True;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = RolePage.ID) and FileExists(ModeFile());
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Mode: String;
begin
  if CurStep <> ssPostInstall then Exit;
  Mode := ExpandConstant('{param:MODE|}');
  if (Mode = '') and not WizardSilent() and not FileExists(ModeFile()) then
  begin
    if RolePage.Values[1] then Mode := 'remote' else Mode := 'here';
  end;
  if (Mode = 'here') or (Mode = 'remote') then
  begin
    ForceDirectories(ExtractFileDir(ModeFile()));
    SaveStringToFile(ModeFile(), Mode, False);
  end;
end;
