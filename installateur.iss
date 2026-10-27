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

; [UninstallRun] retire en 1.4.4 : le nettoyage est lance depuis [Code] (CurUninstallStepChanged),
; avec un nettoyage de secours fait par le desinstalleur lui-meme si l'appli ne demarre pas
; (vu chez Luxo le 27/10/2026 : "Failed to load Python DLL", le mod restait dans le dossier mods).

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

// ---------------------------------------------------------------- desinstallation (1.4.4)
const
  RunKey  = 'Software\Microsoft\Windows\CurrentVersion\Run';
  ModFile = 'FS25_StreamDashboard.zip';

// ferme l'appli si elle tourne (meme methode que pour l'installation)
procedure CloseRunningApp;
var
  Code: Integer;
begin
  Exec('powershell.exe', '-NoProfile -WindowStyle Hidden -Command "try { Invoke-WebRequest -Method Post -Uri http://127.0.0.1:8765/app/quit -UseBasicParsing -TimeoutSec 2 | Out-Null } catch {}; Start-Sleep -Seconds 2"',
       '', SW_HIDE, ewWaitUntilTerminated, Code);
end;

procedure AddDir(L: TStringList; Dir: String);
var
  D: String;
begin
  D := Trim(Dir);
  StringChangeEx(D, '/', '\', True);
  D := RemoveBackslashUnlessRoot(D);
  if (D <> '') and (L.IndexOf(D) < 0) then
    L.Add(D);
end;

// texte du 1er "..." trouve dans S (les \\ du JSON redeviennent \)
function FirstQuoted(Text: String): String;
var
  i, j: Integer;
  S, V: String;
begin
  Result := '';
  i := Pos('"', Text);
  if i = 0 then exit;
  S := Copy(Text, i + 1, Length(Text));
  j := Pos('"', S);
  if j = 0 then exit;
  V := Copy(S, 1, j - 1);
  StringChangeEx(V, '\\', '\', True);
  Result := V;
end;

// tous les "..." de S (liste "installedIn" de app.json)
procedure AddAllQuoted(L: TStringList; Text: String);
var
  i, j: Integer;
  S, V: String;
begin
  S := Text;
  i := Pos('"', S);
  while i > 0 do
  begin
    S := Copy(S, i + 1, Length(S));
    j := Pos('"', S);
    if j = 0 then exit;
    V := Copy(S, 1, j - 1);
    StringChangeEx(V, '\\', '\', True);
    AddDir(L, V);
    S := Copy(S, j + 1, Length(S));
    i := Pos('"', S);
  end;
end;

// dossiers mods notes par l'appli dans app.json ("installedIn" et "modsDir")
procedure DirsFromAppJson(L: TStringList; FileName: String);
var
  Raw: AnsiString;
  T, R: String;
  k, b, e: Integer;
begin
  if not LoadStringFromFile(FileName, Raw) then exit;
  T := Raw;
  k := Pos('"installedIn"', T);
  if k > 0 then
  begin
    R := Copy(T, k + 13, Length(T));
    b := Pos('[', R);
    e := Pos(']', R);
    if (b > 0) and (e > b) then
      AddAllQuoted(L, Copy(R, b + 1, e - b - 1));
  end;
  k := Pos('"modsDir"', T);
  if k > 0 then
  begin
    R := Copy(T, k + 9, Length(T));
    b := Pos(':', R);
    if b > 0 then
      AddDir(L, FirstQuoted(Copy(R, b + 1, Length(R))));
  end;
end;

// dossier mods utilise par le jeu : ligne "Mod Directory: ..." de log.txt
procedure DirFromGameLog(L: TStringList; FileName: String);
var
  Lines: TArrayOfString;
  i, p, n: Integer;
begin
  if not LoadStringsFromFile(FileName, Lines) then exit;
  n := GetArrayLength(Lines);
  if n > 3000 then n := 3000;
  for i := 0 to n - 1 do
  begin
    p := Pos('Mod Directory:', Lines[i]);
    if p > 0 then
    begin
      AddDir(L, Copy(Lines[i], p + 14, Length(Lines[i])));
      exit;
    end;
  end;
end;

// nettoyage fait par le desinstalleur lui-meme, quand l'appli n'a pas pu demarrer
procedure FallbackCleanup;
var
  Dirs: TStringList;
  Profile, DataDir, F, Done, Failed: String;
  i: Integer;
begin
  Done := '';
  Failed := '';
  Profile := ExpandConstant('{userdocs}\My Games\FarmingSimulator2025');
  DataDir := ExpandConstant('{userdocs}\{#AppName}');

  // 1. lancement avec Windows
  if RegDeleteValue(HKEY_CURRENT_USER, RunKey, '{#AppName}') then
    Done := Done + '- lancement avec Windows désactivé' + #13#10;

  // 2. le mod, dans tous les dossiers connus
  Dirs := TStringList.Create;
  try
    DirsFromAppJson(Dirs, DataDir + '\app.json');
    DirFromGameLog(Dirs, Profile + '\log.txt');
    AddDir(Dirs, Profile + '\mods');
    for i := 0 to Dirs.Count - 1 do
    begin
      F := AddBackslash(Dirs[i]) + ModFile;
      if FileExists(F) then
      begin
        if DeleteFile(F) then
          Done := Done + '- mod retiré : ' + F + #13#10
        else
          Failed := Failed + '- mod non retiré (jeu ouvert ?) : ' + F + #13#10;
      end;
    end;
  finally
    Dirs.Free;
  end;

  // 3. donnees ecrites par le mod
  F := Profile + '\modSettings\FS25_StreamDashboard';
  if DirExists(F) then
  begin
    DelTree(F, True, True, True);
    if DirExists(F) then
      Failed := Failed + '- données du mod non effacées : ' + F + #13#10
    else
      Done := Done + '- données du mod effacées' + #13#10;
  end;

  // 4. reglages perso (au choix ; gardes en desinstallation silencieuse)
  if DirExists(DataDir) and (not UninstallSilent) then
  begin
    if MsgBox('Garder tes réglages et tes images perso ?' + #13#10 +
              '(utile si tu réinstalles plus tard)' + #13#10#13#10 + 'Dossier : ' + DataDir,
              mbConfirmation, MB_YESNO) = IDNO then
    begin
      DelTree(DataDir, True, True, True);
      if DirExists(DataDir) then
        Failed := Failed + '- réglages non effacés : ' + DataDir + #13#10
      else
        Done := Done + '- réglages effacés' + #13#10;
    end
    else
      Done := Done + '- réglages gardés : ' + DataDir + #13#10;
  end;

  if not UninstallSilent then
  begin
    F := 'Désinstallation terminée.' + #13#10#13#10 + Done;
    if Failed <> '' then
      F := F + #13#10 + 'À faire à la main :' + #13#10 + Failed;
    F := F + #13#10 + 'Sur un serveur dédié, retire aussi le mod depuis son panneau.';
    MsgBox(F, mbInformation, MB_OK);
  end;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Code: Integer;
  Ok: Boolean;
begin
  if CurUninstallStep = usUninstall then
  begin
    CloseRunningApp;
    // l'appli fait le menage elle-meme (mod dans tous les dossiers, donnees, reglages au choix) ;
    // si Windows l'empeche de demarrer (fichier non signe bloque...), le desinstalleur le fait a sa place
    Ok := Exec(ExpandConstant('{app}\{#AppExe}'), '--uninstall', '', SW_SHOWNORMAL, ewWaitUntilTerminated, Code);
    if (not Ok) or (Code <> 0) then
      FallbackCleanup;
  end;
end;
