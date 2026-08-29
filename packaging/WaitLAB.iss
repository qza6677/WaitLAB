#define MyAppName "WaitLAB"
#ifndef MyAppSourceDir
  #define MyAppSourceDir "..\release"
#endif
#ifndef MyAppOutputDir
  #define MyAppOutputDir "..\release"
#endif
#ifndef MyAppVersion
  #define MyAppVersion GetFileVersion(AddBackslash(MyAppSourceDir) + "WaitLAB.exe")
#endif
#define MyAppPublisher "WaitLAB"
#define MyAppExeName "WaitLAB.exe"

[Setup]
AppId={{D944B1C2-EF36-44B8-B51B-A73A558D2FB5}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#MyAppOutputDir}
OutputBaseFilename=WaitLAB-Setup-{#MyAppVersion}
SetupIconFile=waitlab.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
ArchitecturesAllowed=x64compatible

[Languages]
Name: "chinesesimp"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项："; Flags: unchecked

[Files]
Source: "{#MyAppSourceDir}\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent
