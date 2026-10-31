; -- Discord-Keep-Alive.iss --
; Inno Setup 7 script for packaging the Tauri-built exe.
; Build the exe first, then run:
;   iscc installer.iss /DExePath="...\discord-keep-alive.exe"

#define ExePath = "target/release/discord-keep-alive.exe"

[Setup]
AppName=Discord Keep-Alive
AppVersion=1.0.0
AppPublisher=vc-keeper
AppCopyright=Copyright (c) 2026 vc-keeper contributors
DefaultDirName={autopf}\Discord Keep-Alive
DefaultGroupName=Discord Keep-Alive
OutputBaseFilename=Discord-Keep-Alive-setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64 arm64
PrivilegesRequired=lowest

[Files]
Source: "{#ExePath}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Discord Keep-Alive"; Filename: "{app}\discord-keep-alive.exe"
Name: "{autodesktop}\Discord Keep-Alive"; Filename: "{app}\discord-keep-alive.exe"

[Run]
Filename: "{app}\discord-keep-alive.exe"; Description: "Launch Discord Keep-Alive"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;