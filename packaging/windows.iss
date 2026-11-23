#ifndef AppVersion
  #error "AppVersion must be supplied by build.ps1"
#endif
[Setup]
AppId=GNSS-CN0-Test-Tool
AppName=GNSS C/N0 Test Tool
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\GNSS CN0 Test Tool
DefaultGroupName=GNSS C/N0 Test Tool
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=GNSS-CN0-Setup-x64
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Files]
Source: "..\dist\GNSS-CN0\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs

[Icons]
Name: "{group}\GNSS C/N0 Test Tool"; Filename: "{app}\GNSS-CN0.exe"
