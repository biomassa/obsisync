; obsisync Windows installer.
; Per-user install: no elevation, and start-on-login writes to HKCU anyway.

!define APPNAME "obsisync"
!ifndef VERSION
  !define VERSION "0.1.3"
!endif

Name "${APPNAME}"
OutFile "..\..\dist\obsisync-setup-${VERSION}.exe"
Unicode True
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\${APPNAME}"
InstallDirRegKey HKCU "Software\${APPNAME}" "InstallDir"
ShowInstDetails show
ShowUnInstDetails show

!include "MUI2.nsh"
!define MUI_ICON "..\..\assets\icon.ico"
!define MUI_UNICON "..\..\assets\icon.ico"

!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "English"

Section "install"
  SetOutPath "$INSTDIR"
  File "..\..\dist\obsisync.exe"

  CreateDirectory "$SMPROGRAMS\${APPNAME}"
  CreateShortcut "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk" "$INSTDIR\obsisync.exe"

  WriteRegStr HKCU "Software\${APPNAME}" "InstallDir" "$INSTDIR"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" \
                   "DisplayName" "${APPNAME}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" \
                   "DisplayVersion" "${VERSION}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" \
                   "UninstallString" "$INSTDIR\uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" \
                   "DisplayIcon" "$INSTDIR\obsisync.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}" \
                   "NoModify" "1"
  WriteUninstaller "$INSTDIR\uninstall.exe"
SectionEnd

Section "un.install"
  ; The app may be sitting in the tray; a running exe cannot be deleted.
  ExecWait 'taskkill /IM obsisync.exe /F' $0

  Delete "$INSTDIR\obsisync.exe"
  Delete "$INSTDIR\uninstall.exe"
  RMDir "$INSTDIR"

  Delete "$SMPROGRAMS\${APPNAME}\${APPNAME}.lnk"
  RMDir "$SMPROGRAMS\${APPNAME}"

  DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${APPNAME}"
  DeleteRegKey HKCU "Software\${APPNAME}"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${APPNAME}"

  ; Config, sync state and cached credentials are deliberately left in place so
  ; a reinstall does not force a fresh bootstrap of the whole vault.
  DetailPrint "Settings kept in %APPDATA%\obsisync and %LOCALAPPDATA%\obsisync"
SectionEnd
