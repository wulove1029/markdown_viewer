#define MyAppName "Markdown Viewer"
#define MyAppVersion "1.38.1"
#define MyAppPublisher "Jerry"
#define MyAppExeName "MarkdownViewer.exe"
#ifndef MySourceDir
#define MySourceDir "dist\MarkdownViewer"
#endif

[Setup]
AppId={{B7A3C2D4-8F1E-4A5B-9C6D-2E0F3A7B8D1C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
AllowNoIcons=yes
OutputDir=installer_output
OutputBaseFilename=MarkdownViewer_Setup_v{#MyAppVersion}
SetupIconFile=ICON\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑"; GroupDescription: "額外圖示："; Flags: unchecked

[Files]
Source: "{#MySourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "ICON\icon.ico"; DestDir: "{app}\ICON"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\ICON\icon.ico"
Name: "{group}\解除安裝 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\ICON\icon.ico"; Tasks: desktopicon

[Registry]
; Register an Open With choice without replacing the user's default handler.
Root: HKCR; Subkey: ".md\OpenWithProgids"; ValueType: none; ValueName: "MarkdownViewer.mdfile"; Flags: uninsdeletevalue
Root: HKCR; Subkey: "MarkdownViewer.mdfile"; ValueType: string; ValueName: ""; ValueData: "Markdown 文件"; Flags: uninsdeletekey
Root: HKCR; Subkey: "MarkdownViewer.mdfile\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\ICON\icon.ico"
Root: HKCR; Subkey: "MarkdownViewer.mdfile\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

Root: HKCR; Subkey: ".markdown\OpenWithProgids"; ValueType: none; ValueName: "MarkdownViewer.mdfile"; Flags: uninsdeletevalue

; markdownviewer://open?path=<percent-encoded absolute path> (lets other apps open a document).
Root: HKA; Subkey: "Software\Classes\markdownviewer"; ValueType: string; ValueName: ""; ValueData: "URL:Markdown Viewer Protocol"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\markdownviewer"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKA; Subkey: "Software\Classes\markdownviewer\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName}"
Root: HKA; Subkey: "Software\Classes\markdownviewer\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\{#MyAppExeName}"" ""%1"""

; App Paths lets other programs locate the executable by name.
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExeName}"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MyAppExeName}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\{#MyAppExeName}"; ValueType: string; ValueName: "Path"; ValueData: "{app}"

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即啟動 {#MyAppName}"; Flags: nowait postinstall skipifsilent
