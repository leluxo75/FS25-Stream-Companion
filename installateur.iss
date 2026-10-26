; Installateur Windows de FS25 Stream & Companion by leluxo (ex FS25 Stream Dashboard) (Inno Setup 6 / 7)
; Fabrique par Fabriquer_App.bat :  ISCC.exe /DAppVersion=1.2.0 installateur.iss

#ifndef AppVersion
  #define AppVersion "1.2.0"
#endif
#define AppName "FS25 Stream Dashboard"        ; ancien nom : garde pour le dossier d'installation (ne pas changer)
#define DisplayName "FS25 Stream & Companion"
#define AppExe  "FS25 Stream Dashboard.exe"

[Setup]
; identifiant fixe : ne jamais le changer (sinon Windows croit a une autre appli)
AppId={{7B6E2F31-4C1D-4E7A-9C25-F25D45B0A11E}
AppName={#DisplayName}
AppVersion={#AppVersion}
AppVerName={#DisplayName} {#AppVersion}
AppPublisher=leluxo
DefaultDirName={localappdata}\Programs\{#AppName}
DefaultGroupName={#DisplayName}
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=.
OutputBaseFilename={#DisplayName} - Installation v{#AppVersion}
SetupIconFile=app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#DisplayName} by leluxo
WizardStyle=modern
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
; force Windows a rafraichir les icones (sinon l'ancien logo reste affiche)
ChangesAssociations=yes

[Languages]
Name: "fr"; MessagesFile: "compiler:Languages\French.isl"

[Tasks]
Name: "desktopicon"; Description: "Créer un raccourci sur le Bureau"; GroupDescription: "Raccourcis :"

[Files]
; 1.4.4 : application en mode DOSSIER (l'exe + le dossier _internal), plus un seul gros exe
Source: "dist\{#AppName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; pieces de l'ancienne version : on vide le dossier avant de mettre les nouvelles (pas de vieux fichiers qui trainent)
Type: filesandordirs; Name: "{app}\_internal"
; raccourcis de l'ancien nom (versions 1.3 et avant)
Type: files; Name: "{userdesktop}\{#AppName}.lnk"
Type: filesandordirs; Name: "{autoprograms}\{#AppName}"

[Icons]
Name: "{group}\{#DisplayName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Désinstaller {#DisplayName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#DisplayName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "Lancer {#DisplayName}"; Flags: nowait postinstall skipifsilent
; mise a jour automatique (installateur lance par l'appli en mode silencieux) : on relance l'appli a la fin
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: WizardSilent

[UninstallRun]
; avant de supprimer l'appli : ferme-la, retire le mod, ses donnees et (au choix) les reglages
Filename: "{app}\{#AppExe}"; Parameters: "--uninstall"; RunOnceId: "NettoyageDashboard"; Flags: waituntilterminated

[Code]
// avant l'installation / mise a jour : ferme l'appli si elle tourne (sinon le fichier est bloque)
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Code: Integer;
begin
  // demande a l'appli lancee (installee ou portable) de se fermer, via son adresse locale
  Exec('powershell.exe', '-NoProfile -WindowStyle Hidden -Command "try { Invoke-WebRequest -Method Post -Uri http://127.0.0.1:8765/app/quit -UseBasicParsing -TimeoutSec 2 | Out-Null } catch {}; Start-Sleep -Seconds 2"',
       '', SW_HIDE, ewWaitUntilTerminated, Code);
  Result := '';
end;
