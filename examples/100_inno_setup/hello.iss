; An Inno Setup script for Hello, compiled by pcons-build.py, which defines
; Version, Readme (a file), and StagingDir (the files to install).

[Setup]
AppId={{2F6A1D8C-4B7E-4E0A-9C3D-5B8E1F2A7C64}
AppName=Hello
AppVersion={#Version}
AppPublisher=pcons examples
; Installs for the current user, so it needs no administrator.
PrivilegesRequired=lowest
DefaultDirName={autopf}\Hello
DisableProgramGroupPage=yes
InfoBeforeFile={#Readme}
; Read, like any relative path here, from this script's directory.
SetupIconFile=hello.ico
UninstallDisplayIcon={app}\hello.exe
WizardStyle=modern

[Files]
Source: "{#StagingDir}\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion
