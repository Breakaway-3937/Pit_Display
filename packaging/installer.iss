; Breakaway Pit Display - Windows installer.
;
; **This is the one file a person is given.** Download it, double-click it,
; answer two questions, done - the same shape as any application off the
; internet, with a Start Menu entry and an Add/Remove Programs entry.
;
; What it deliberately does NOT do is put the app in one folder and stop.
; It builds the versioned layout the self-updater needs:
;
;     {localappdata}\Programs\Breakaway Pit Display\
;         pointer.json
;         current  -----> versions\1.4.2      (a directory junction)
;         versions\1.4.2\Breakaway Pit Display.exe
;
; Every shortcut points at `current\`, so from this moment on an update is a
; link being repointed and nobody ever copies a folder again. See
; app/update/install.py for why that shape, and DEPLOYMENT.md for the rest.
;
; Four things about this script are load bearing:
;
;   * **PrivilegesRequired=lowest.** Per-user, under LOCALAPPDATA. A pit laptop
;     operator is not an administrator, and an install that needs elevation
;     needs it again on every update - which is the whole thing being avoided.
;   * **The junction is removed with `rmdir`, never by Inno's own file
;     deletion.** `Type: filesandordirs` on a junction descends into the target
;     and deletes the version behind it. CurUninstallStepChanged removes the
;     reparse point before Inno touches anything.
;   * **The data directory is never written except for the token.** The
;     database, checklists, CAN-id names, imported logs and the uploaded CAD
;     model live there, and an installer that cleared it would throw away a
;     season on an upgrade. The uninstaller asks before removing it, and
;     defaults to no.
;   * **[Code] comments are `//`, never braces, and the file is pure ASCII.**
;     Inno's Pascal uses `{ }` for comments, so a brace-comment mentioning a
;     constant like the app dir ENDS at that constant's own brace and the rest
;     of the sentence is compiled as code. That is exactly how the first build
;     of this file failed - "Syntax error", 50 columns into a comment. Non-ASCII
;     is banned for the same reason: Inno needs a UTF-8 BOM to read it, and a
;     build script should not gamble on file encoding. `tools/check_installer.py`
;     enforces both, so the next occurrence is caught here rather than in CI.
;
; Built by `tools/build_app.py --installer`, which passes the two defines.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\Breakaway Pit Display"
#endif
; Windows' own version resource must be four numbers, so a prerelease like
; 1.4.2-beta.1 cannot go in it. build_app.py passes the numeric part; AppVersion
; keeps the real string, and that is the one shown everywhere a person reads.
#ifndef NumericVersion
  #define NumericVersion "0.0.0.0"
#endif

#define AppName    "Breakaway Pit Display"
#define AppExeName "Breakaway Pit Display.exe"
#define AppPublisher "Breakaway 3937"
#define DataDirName "Breakaway Pit Display"

[Setup]
; Never change this GUID - it is how Windows knows an upgrade from a second
; copy, and changing it would leave two entries in Add/Remove Programs.
AppId={{7B4C1E2A-3F55-4C8D-9E31-6A9D2F0B7C14}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#NumericVersion}
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Breakaway-Pit-Display-{#AppVersion}-Setup
; lzma2/normal without solid compression: the payload is ~850 MB of Qt and
; Chromium, and solid mode spends another ten minutes of CI time to save a
; couple of percent on something nobody stores twice.
Compression=lzma2/normal
SolidCompression=no
WizardStyle=modern
UninstallDisplayName={#AppName}
; Nothing needs closing. Files land in a *new* versions\ folder and only the
; junction moves, so an upgrade over a running pit display is safe - and being
; told to close the display in front of visitors is not smooth.
CloseApplications=no
#if FileExists(AddBackslash(SourcePath) + "icon.ico")
SetupIconFile=icon.ico
UninstallDisplayIcon={app}\current\{#AppExeName}
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "startupicon"; Description: "Start the pit display when Windows starts"; GroupDescription: "The pit machine:"
Name: "desktopicon"; Description: "Put a shortcut on the desktop"; GroupDescription: "The pit machine:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}\versions\{#AppVersion}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; Every shortcut goes through `current`, never through a version folder -
; that is what makes tomorrow's update the thing these shortcuts open.
Name: "{group}\{#AppName}"; Filename: "{app}\current\{#AppExeName}"; WorkingDir: "{app}\current"
Name: "{userdesktop}\{#AppName}"; Filename: "{app}\current\{#AppExeName}"; WorkingDir: "{app}\current"; Tasks: desktopicon
Name: "{userstartup}\{#AppName}"; Filename: "{app}\current\{#AppExeName}"; WorkingDir: "{app}\current"; Tasks: startupicon

[Run]
Filename: "{app}\current\{#AppExeName}"; Description: "Start the pit display now"; Flags: nowait postinstall skipifsilent

[Code]
var
  TokenPage: TInputQueryWizardPage;

procedure InitializeWizard;
begin
  TokenPage := CreateInputQueryPage(wpSelectTasks,
    'Automatic updates',
    'How this machine gets future versions.',
    'The repository is private, so this machine needs a GitHub token to see new releases.' + #13#10 +
    'Paste one here and updates arrive by themselves. Leave it blank to skip - you can add' + #13#10 +
    'it later from Control > Pit Systems > Software Updates.');
  TokenPage.Add('GitHub token (optional):', False);
end;

function DataDir(): String;
begin
  Result := ExpandConstant('{localappdata}\{#DataDirName}');
end;

// The layout marker. Its presence is how app/update/install.py recognises a
// managed install; without it the app correctly refuses to update itself.
procedure WritePointer();
var
  Path: String;
begin
  Path := ExpandConstant('{app}\pointer.json');
  SaveStringToFile(Path,
    '{' + #13#10 +
    '  "current": "{#AppVersion}",' + #13#10 +
    '  "previous": "",' + #13#10 +
    '  "updated": "' + GetDateTimeString('yyyy-mm-dd', '-', ':') + 'T00:00:00Z"' + #13#10 +
    '}' + #13#10, False);
end;

// A directory junction, not a symlink: junctions need no privilege, and this
// installer deliberately has none.
function MakeJunction(): Boolean;
var
  Code: Integer;
  Link, Target: String;
begin
  Link := ExpandConstant('{app}\current');
  Target := ExpandConstant('{app}\versions\{#AppVersion}');
  // rmdir removes only the reparse point. Anything that recurses would walk
  // into the target and delete the version it points at.
  if DirExists(Link) then
    Exec(ExpandConstant('{cmd}'), '/c rmdir "' + Link + '"', '',
         SW_HIDE, ewWaitUntilTerminated, Code);
  Result := Exec(ExpandConstant('{cmd}'),
                 '/c mklink /J "' + Link + '" "' + Target + '"', '',
                 SW_HIDE, ewWaitUntilTerminated, Code) and DirExists(Link);
end;

procedure SaveToken();
var
  Token: String;
begin
  Token := Trim(TokenPage.Values[0]);
  if Token = '' then
    Exit;
  ForceDirectories(DataDir());
  // Beside the database, not inside the app folder, so an update - which
  // replaces the app folder wholesale - never loses it.
  SaveStringToFile(DataDir() + '\update_token', Token, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    WritePointer();
    if not MakeJunction() then
      MsgBox('Setup could not create the launcher link.' + #13#10#13#10 +
             'The app is installed at ' + ExpandConstant('{app}\versions\{#AppVersion}') +
             ' and will run from there, but it will not be able to update itself.',
             mbError, MB_OK);
    SaveToken();
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
  Link: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    // Before Inno deletes anything: a junction removed the wrong way takes
    // the target's contents with it.
    Link := ExpandConstant('{app}\current');
    if DirExists(Link) then
      Exec(ExpandConstant('{cmd}'), '/c rmdir "' + Link + '"', '',
           SW_HIDE, ewWaitUntilTerminated, Code);
  end;

  if CurUninstallStep = usPostUninstall then
  begin
    // Versions the self-updater installed later are not in Inno's file list,
    // so nothing else would ever remove them. The app dir itself is left to
    // Inno, which cannot delete the uninstaller it is running from.
    DelTree(ExpandConstant('{app}\versions'), True, True, True);
    DeleteFile(ExpandConstant('{app}\pointer.json'));

    if DirExists(DataDir()) then
      if MsgBox('Also delete this machine''s data?' + #13#10#13#10 +
                'That is the database, the checklists, the CAN-id names, every ' +
                'imported robot log, the EQ presets and the uploaded CAD model.' + #13#10#13#10 +
                'Say No if you are reinstalling.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir(), True, True, True);
  end;
end;
