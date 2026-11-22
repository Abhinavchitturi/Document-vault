; Document Vault setup for Windows (Inno Setup 6): DocumentVault-Setup.exe.
;   powershell -File packaging\windows\build_engine.ps1                       (-> dist\engine)
;   dotnet publish apps\windows\DocumentVault.App ... -o dist\windows-app
;   ISCC.exe /DVersion=1.0.0 packaging\windows\DocumentVault.iss
;
; Just the native app (WinUI), like any Windows app: no administrator rights, and nothing else installed (no Git,
; Node, Python or winget). Its first page asks whether this PC keeps the vault or only connects to the vault on
; another computer. Only a PC that keeps the vault downloads the vault's engine, from the app, with a progress bar
; (DocumentVault-Engine-Windows.zip, checked against the SHA-256 in the app's engine.json), into
; %LOCALAPPDATA%\Document Vault\Engine. Your documents live in %LOCALAPPDATA%\Document Vault\Vault: uninstalling
; or updating removes the program and its engine, never that folder.
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
Source: "DocumentVault.ico"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Document Vault"; Filename: "{app}\App\DocumentVault.exe"; WorkingDir: "{app}\App"
Name: "{autodesktop}\Document Vault"; Filename: "{app}\App\DocumentVault.exe"; WorkingDir: "{app}\App"; Tasks: desktopicon

[Run]
; an update the app started (/UPDATE=1, quiet): open the app again when it's done
Filename: "{app}\App\DocumentVault.exe"; WorkingDir: "{app}\App"; Flags: nowait; Check: IsUpdate
Filename: "{app}\App\DocumentVault.exe"; Description: "Open Document Vault"; WorkingDir: "{app}\App"; \
  Flags: postinstall nowait skipifsilent

[InstallDelete]
; the engine used to be installed next to the app; the app now downloads it when the PC keeps the vault
Type: filesandordirs; Name: "{app}\Engine"

[UninstallDelete]
; the engine the app downloaded (never the vault: %LOCALAPPDATA%\Document Vault\Vault)
Type: filesandordirs; Name: "{localappdata}\Document Vault\Engine"
; this PC's answer to "keep the vault here, or connect?" and its pairing with another computer: asked again next time
Type: files; Name: "{localappdata}\Document Vault\mode.txt"
Type: files; Name: "{localappdata}\Document Vault\connection.bin"
Type: filesandordirs; Name: "{app}\Engine"

[Code]
// First question of the setup: is this PC the centre (the vault and its documents live here), or does it only use
// the vault on another computer? Saved as the app's own first-start choice (%LOCALAPPDATA%\Document Vault\mode.txt),
// so the app opens straight to the right screen; it can be changed later in the app's Settings. It is asked on every
// install, with nothing chosen beforehand: Next waits for an answer. A silent install asks nothing (the app asks on
// its first start), unless it is given /MODE=here or /MODE=remote. Uninstalling forgets the answer (never the
// documents).
var
  RolePage: TInputOptionWizardPage;

function IsUpdate(): Boolean;
begin
  Result := ExpandConstant('{param:UPDATE|0}') = '1';
end;

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
    'phones in the family can then connect to it. The app then downloads the vault''s engine (about 370 MB, ' +
    'once). Choose the other option if this PC should only use the family''s vault on another computer (the ' +
    'centre): nothing else is downloaded and nothing is stored here.',
    True, False);
  RolePage.Add('Keep the vault on this PC (this PC is the centre)');
  RolePage.Add('Use the vault on another computer (just connect to it)');
  RolePage.Values[0] := False;  // nothing chosen beforehand
  RolePage.Values[1] := False;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  // (Setup calls this for every page when it runs silently too: there is no one to ask then, and nothing is chosen)
  if (CurPageID = RolePage.ID) and not WizardSilent() and not RolePage.Values[0] and not RolePage.Values[1] then
  begin
    MsgBox('Choose how this PC will use Document Vault.', mbInformation, MB_OK);
    Result := False;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  Mode: String;
begin
  if CurStep <> ssPostInstall then Exit;
  Mode := ExpandConstant('{param:MODE|}');
  if (Mode = '') and not WizardSilent() then
  begin
    if RolePage.Values[1] then Mode := 'remote' else Mode := 'here';
  end
  else if Mode = '' then
    DeleteFile(ModeFile());  // a silent install: the app asks on its first start
  if (Mode = 'here') or (Mode = 'remote') then
  begin
    ForceDirectories(ExtractFileDir(ModeFile()));
    SaveStringToFile(ModeFile(), Mode, False);
  end;
end;
