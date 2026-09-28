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
; The libraries, downloaded while the installer is still on screen. About
; 200 MB, so the window stays up and shows what pip is doing -- a silent
; ten-minute pause looks like a hang.
Filename: "{app}\install-libraries.bat"; \
    Description: "Download the required libraries (about 200 MB)"; \
    StatusMsg: "Downloading libraries from PyPI..."; \
    Flags: postinstall runascurrentuser
; The models are left to the app: it shows a proper window with progress,
; and a user who wants to wait for better wifi can say not now.
Filename: "{app}\{#AppExe}"; Description: "Start {#AppName}"; \
    Flags: postinstall nowait skipifsilent unchecked

[UninstallDelete]
; Installed wheels live under the app folder, so they go with it. What the
; user made -- transcripts, settings, corrections, models -- is in
; %LOCALAPPDATA%\LiveTranslator and is deliberately left behind.
Type: filesandordirs; Name: "{app}\runtime\Lib\site-packages"
Type: filesandordirs; Name: "{app}\livetranslator\__pycache__"
