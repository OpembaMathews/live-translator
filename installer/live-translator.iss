; Inno Setup script for Live Translator.
;
; The installer is small on purpose. It carries the app, a private Python
; and nothing else: the libraries are installed from PyPI while the
; installer runs, and the models are fetched by the app on its first run.
; Bundling either would make a download most people abandon.
;
; Build it with installer\build.ps1, which prepares the payload first.

#define AppName      "Live Translator"
#define AppVersion   "1.0.0"
#define AppPublisher "Opemba Mathews"
#define AppExe       "LiveTranslator.bat"

[Setup]
AppId={{8F3A6C21-9E4B-4D77-9C6E-2B1A7D5E8C40}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
; Per-user by default, so no administrator password is needed. A student on
; a shared or managed laptop can install it without asking anyone.
PrivilegesRequired=lowest
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=LiveTranslator-{#AppVersion}-setup
SetupIconFile=..\assets\translator.ico
UninstallDisplayIcon={app}\assets\translator.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; \
    GroupDescription: "Shortcuts:"
; Downloading the models here rather than on first run means one wait, at a
; moment the user is already waiting, instead of a surprise the first time
; they try to read a paper. It can be skipped: someone installing on a phone
; tether would rather do it later, and the app asks again if they do.
Name: "models"; Description: \
    "Download the voice and translation packs now (about 650 MB)"; \
    GroupDescription: "Setup:"

[Files]
; The app itself
Source: "..\livetranslator\*"; DestDir: "{app}\livetranslator"; \
    Flags: ignoreversion recursesubdirs createallsubdirs; \
    Excludes: "__pycache__,*.pyc"
Source: "..\assets\*";  DestDir: "{app}\assets";  Flags: ignoreversion recursesubdirs
Source: "..\data\*";    DestDir: "{app}\data";    Flags: ignoreversion recursesubdirs
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md";        DestDir: "{app}"; Flags: ignoreversion
Source: "..\docs\*";    DestDir: "{app}\docs";    Flags: ignoreversion recursesubdirs
; The private Python, prepared by build.ps1
Source: "payload\runtime\*"; DestDir: "{app}\runtime"; \
    Flags: ignoreversion recursesubdirs createallsubdirs
; Launchers
Source: "payload\LiveTranslator.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "payload\Reader.bat";         DestDir: "{app}"; Flags: ignoreversion
Source: "payload\install-libraries.bat"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
    IconFilename: "{app}\assets\translator.ico"
Name: "{group}\Paper Reader"; Filename: "{app}\Reader.bat"; \
    IconFilename: "{app}\assets\translator.ico"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
    IconFilename: "{app}\assets\translator.ico"; Tasks: desktopicon

[Run]
; The libraries are installed by the [Code] section below, on the wizard's
; own progress page. install-libraries.bat stays on disk only so a download
; that failed can be retried by hand.
;
; The models are left to the app: it shows its own window with progress,
; and someone who would rather wait for better wifi can say not now.
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; \
    Flags: postinstall nowait skipifsilent unchecked

[UninstallDelete]
; Installed wheels live under the app folder, so they go with it. What the
; user made -- transcripts, settings, corrections, models -- is in
; %LOCALAPPDATA%\LiveTranslator and is deliberately left behind.
Type: filesandordirs; Name: "{app}\runtime\Lib\site-packages"
Type: filesandordirs; Name: "{app}\livetranslator\__pycache__"
Type: files; Name: "{app}\library-install.log"
Type: files; Name: "{app}\model-download.log"

[Code]
// pip runs hidden and reports on the wizard's own progress page. Letting it
// open a console window was the obvious way to do it, but a black window
// appearing part way through an install looks like something has gone
// wrong.
//
// Exec cannot report progress while it waits, so pip is started without
// waiting, writes to a log, and drops a "done" file holding its exit code.
// The loop below follows the log until that file appears.

function LastLineOf(const Path: String): String;
var
  Lines: TArrayOfString;
  I: Integer;
begin
  Result := '';
  if not LoadStringsFromFile(Path, Lines) then
    Exit;
  for I := GetArrayLength(Lines) - 1 downto 0 do
    if Trim(Lines[I]) <> '' then
    begin
      Result := Trim(Lines[I]);
      Exit;
    end;
end;

function PercentIn(const Line: String): Integer;
var
  I, Start: Integer;
begin
  // The downloader prints "kokoro-v1.0.onnx (1 of 6): 45%" when nothing is
  // watching. Reading that number back gives a bar that means something;
  // before this it was a marquee that cycled whatever was happening, which
  // told the user only that the installer had not frozen.
  Result := -1;
  I := Length(Line);
  while (I > 0) and (Line[I] = ' ') do
    I := I - 1;
  if (I = 0) or (Line[I] <> '%') then
    Exit;
  I := I - 1;
  Start := I;
  while (Start > 0) and (Line[Start] >= '0') and (Line[Start] <= '9') do
    Start := Start - 1;
  if Start = I then
    Exit;
  Result := StrToIntDef(Copy(Line, Start + 1, I - Start), -1);
end;

function ExitCodeIn(const Path: String): Integer;
var
  Lines: TArrayOfString;
begin
  Result := -1;
  if LoadStringsFromFile(Path, Lines) and (GetArrayLength(Lines) > 0) then
    Result := StrToIntDef(Trim(Lines[0]), -1);
end;

procedure InstallLibraries;
var
  Page: TOutputProgressWizardPage;
  LogPath, DonePath, Command, Line: String;
  Code, Tick: Integer;
begin
  LogPath := ExpandConstant('{app}\library-install.log');
  DonePath := ExpandConstant('{app}\library-install.done');
  DeleteFile(LogPath);
  DeleteFile(DonePath);

  Page := CreateOutputProgressPage(
    'Downloading libraries',
    'About 200 MB from PyPI. This happens once; they are kept afterwards.');
  Page.SetText('Starting...', '');
  Page.Show;
  try
    Command := '/c ""' + ExpandConstant('{app}\runtime\python.exe') +
      '" -m pip install --no-warn-script-location -r "' +
      ExpandConstant('{app}\requirements.txt') + '" > "' + LogPath +
      '" 2>&1 & echo %errorlevel% > "' + DonePath + '""';

    if not Exec(ExpandConstant('{cmd}'), Command, ExpandConstant('{app}'),
                SW_HIDE, ewNoWait, Code) then
    begin
      MsgBox('The libraries could not be installed: pip would not start.' + #13#10 +
             'Run install-libraries.bat in the install folder to try again.',
             mbError, MB_OK);
      Exit;
    end;

    Tick := 0;
    while not FileExists(DonePath) do
    begin
      Line := LastLineOf(LogPath);
      if Line = '' then
        Line := 'Contacting PyPI...';
      // pip's lines are long, so only what fits is shown. The bar keeps
      // moving because pip gives no overall total to measure against.
      Page.SetText(Copy(Line, 1, 90), '');
      Tick := (Tick + 2) mod 100;
      Page.SetProgress(Tick, 100);
      Sleep(350);
    end;

    Code := ExitCodeIn(DonePath);
    DeleteFile(DonePath);
    if Code <> 0 then
      MsgBox('The libraries did not finish downloading.' + #13#10#13#10 +
             'What arrived is kept, so trying again carries on from there: ' +
             'run install-libraries.bat in the install folder.' + #13#10#13#10 +
             'The details are in library-install.log.',
             mbError, MB_OK);
  finally
    Page.Hide;
  end;
end;

procedure DownloadModels;
var
  Page: TOutputProgressWizardPage;
  LogPath, DonePath, Command, Line: String;
  Code, Tick, Percent: Integer;
begin
  LogPath := ExpandConstant('{app}\model-download.log');
  DonePath := ExpandConstant('{app}\model-download.done');
  DeleteFile(LogPath);
  DeleteFile(DonePath);

  Page := CreateOutputProgressPage(
    'Downloading the voice and translation packs',
    'About 650 MB. They are kept, so this happens once.');
  Page.SetText('Starting...', '');
  Page.Show;
  try
    Command := '/c ""' + ExpandConstant('{app}\runtime\python.exe') +
      '" -m livetranslator.models > "' + LogPath +
      '" 2>&1 & echo %errorlevel% > "' + DonePath + '""';

    if not Exec(ExpandConstant('{cmd}'), Command, ExpandConstant('{app}'),
                SW_HIDE, ewNoWait, Code) then
      Exit;

    Tick := 0;
    while not FileExists(DonePath) do
    begin
      Line := LastLineOf(LogPath);
      if Line = '' then
        Line := 'Contacting the download servers...';
      Page.SetText(Copy(Line, 1, 90), '');
      Percent := PercentIn(Line);
      if Percent >= 0 then
        Page.SetProgress(Percent, 100)
      else
      begin
        // No figure yet: looking up the index, or unzipping a pack. The
        // bar creeps so the window does not look frozen.
        Tick := (Tick + 2) mod 100;
        Page.SetProgress(Tick, 100);
      end;
      Sleep(400);
    end;

    Code := ExitCodeIn(DonePath);
    DeleteFile(DonePath);
    if Code <> 0 then
      // Never start a line with #: Inno's preprocessor reads that as a
      // directive, whatever indentation is in front of it.
      MsgBox('The voice and translation packs did not finish downloading.'
             + #13#10#13#10 + 'What arrived is kept, and the app offers to '
             + 'finish the download the next time it starts.'
             + #13#10#13#10 + 'The details are in model-download.log.',
             mbInformation, MB_OK);
  finally
    Page.Hide;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    // Order matters. The downloader is part of the app and imports the
    // translation engine, so it cannot run until pip has finished.
    InstallLibraries;
    if WizardIsTaskSelected('models') then
      DownloadModels;
  end;
end;
