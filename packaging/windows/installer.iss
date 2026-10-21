; NeverRed — instalador de Windows (Inno Setup 6).
; Compila con:
;   ISCC.exe /DAppVersion=2.6.8 /DPayload=C:\NeverRed\src\dist\windows\NeverRed installer.iss
; El alude {#Payload} es el árbol ya construido por build.ps1
; (NeverRed.exe + NeverRed-Update.exe + app + backend + cacert.pem).
; Instalación por usuario (sin elevación) en %LOCALAPPDATA%\NeverRed:
; el actualizador renombra su propio directorio en cada update, para lo que
; necesita permisos de escritura (Program Files no lo permitiría sin UAC).

#define AppVersionParam
#define AppName "NeverRed"
#define AppGuid "{9E7A2C1F-4B8D-4E3A-9F1C-6D2B5A7C8E90}"

[Setup]
AppId={{#AppGuid}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppName}
AppSupportURL=https://github.com/emilio-devesa/NeverRed/releases
; Por usuario, sin UAC:
PrivilegesRequired=lowest
DefaultDirName={localappdata}\{#AppName}
DisableProgramGroupPage=yes
; Establecer sí mismo como apagable: el launcher mantiene el mutex
; NeverRedAppMutex (ver packaging/windows/launcher.py) mientras corre.
AppMutex=NeverRedAppMutex
CloseApplications=force
RestartApplications=no
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\NeverRed.exe
OutputBaseFilename=NeverRed-setup-{#AppVersion}

[Languages]
Name: "spanish"; MessagesFile: "compiler:Languages\Spanish.isl"

[Tasks]
Name: "desktopicon"; Description: "Crear acceso directo en el escritorio"
Name: "startmenu"; Description: "Crear acceso directo en el menú de inicio"; Flags: checkedonce

[Files]
; recursesubdirs: assets/ y backend/ van dentro de {app} exactamente como en
; el ZIP del exe (raíz plana, estructuras anidadas).
Source: "{#Payload}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\NeverRed.exe"; WorkingDir: "{app}"; Tasks: startmenu
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\NeverRed.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\NeverRed.exe"; WorkingDir: "{app}"; Flags: nowait postinstall skipifsilent; Description: "Ejecutar {#AppName}"

[UninstallRun]
; El desinstalador de Inno NO aplica CloseApplications/AppMutex (solo el
; setup); con VERYSILENT aborta rc=1 si los ficheros están en uso. Este
; taskkill corre ANTES de borrar ficheros y garantiza el cierre correcto
; tanto del launcher como del hijo --serve (mismo .exe).
Filename: "taskkill.exe"; Parameters: "/F /IM NeverRed.exe /T"; Flags: runhidden

[Code]
// Barra de seguridad extra: si aún hay proceso vivo, matarlo en la
// inicialización de la desinstalación (antes de todo lo demás).
function InitializeUninstall(): Boolean;
var
  Res: Integer;
begin
  Exec('taskkill.exe', '/F /IM NeverRed.exe /T', '', SW_HIDE,
       ewWaitUntilTerminated, Res);
  Result := True;
end;

[UninstallDelete]
; Restos de un intercambio de update a medias: viven JUNTO a {app}
; (%LOCALAPPDATA%\.NeverRed.update-old / -new). Más allá no se borra
; nada: los datos del usuario (%APPDATA%\NeverRed) se conservan siempre.
Type: filesandordirs; Name: "{localappdata}\.NeverRed.update-old"
Type: filesandordirs; Name: "{localappdata}\.NeverRed.update-new"
