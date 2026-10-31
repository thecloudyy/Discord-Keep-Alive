; -- Discord-Keep-Alive.iss --
; Inno Setup 6+ script for packaging the Tauri-built exe.
; Build the exe first, then run:
;   iscc installer.iss  [or pass /DExePath=...\discord-keep-alive.exe]

[Setup]
AppName=Discord Keep-Alive
AppVersion=1.0.0
AppPublisher=vc-keeper
AppCopyright=Copyright (c) 2026 vc-keeper contributors
DefaultDirName={autofolder}\Discord Keep-Alive
DefaultGroupName=Discord Keep-Alive
OutputBaseFilename=Discord-Keep-Alive-setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesAllowed/arm64
PrivilegesRequired=lowest

[Files]
; The Tauri-built exe. If /DExePath isn't set, fall back to the default release output.
Source: "{param:ExePath|src-tauri/target/release/discord-keep-alive.exe}"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Discord Keep-Alive"; Filename: "{app}\discord-keep-alive.exe"
Name: "{autodesktop}\Discord Keep-Alive"; Filename: "{app}\discord-keep-alive.exe"

[Run]
Filename: "{app}\discord-keep-alive.exe"; Description: "Launch Discord Keep-Alive"; Flags: nowait postinstall skipifsilent

[Code]
function InitializeSetup(): Boolean;
begin
  // Allow overriding the exe path via command line: /DExePath="C:\path\to.exe"
  Result := True;
end;