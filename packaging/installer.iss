; Inno Setup 6 script for Simple PI Calculator.
;
; Build the PyInstaller onedir bundle first (dist/SimplePICalculator/...),
; then compile with the version passed on the command line, e.g.:
;
;   iscc /DMyAppVersion=0.1.0 installer.iss
;
; Run from the "packaging" directory (paths below are relative to it).
; Per docs/DESIGN.md §7.2.

#define MyAppName "Simple PI Calculator"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0"
#endif
#define MyAppPublisher "Simple PI Calculator contributors"
#define MyAppExeName "SimplePICalculator.exe"

[Setup]
; This GUID is fixed for the life of the product — never change it.
AppId={{B1942B9F-0A78-4305-863E-D4ED531EF3E0}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\Simple PI Calculator
DefaultGroupName=Simple PI Calculator
; Allow the user to choose a per-machine or per-user install at run time.
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
OutputDir=..\dist-installer
OutputBaseFilename=SimplePICalculator-Setup-{#MyAppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\src\simple_pi_calculator\resources\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
; No file association in v1: the double extension ".spical.json" cannot be
; associated reliably (see docs/DESIGN.md §7.2).

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; Flags: unchecked

[Files]
Source: "..\dist\SimplePICalculator\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Simple PI Calculator"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\Examples"; Filename: "{app}\_internal\examples"
Name: "{group}\Uninstall Simple PI Calculator"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Simple PI Calculator"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch Simple PI Calculator"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Remove any leftover log/temp files the app writes next to itself, if any.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

; The uninstaller intentionally does NOT touch the per-user auto-save folder
; %APPDATA%\SimplePICalculator, so a reinstall/upgrade keeps the user's
; project state (docs/DESIGN.md §7.2, §5.8).
