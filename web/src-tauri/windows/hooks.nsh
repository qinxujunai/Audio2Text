; Replace the owned PyInstaller runtime atomically instead of overlaying DLLs.
; Keep the previous runtime and main executable for manual recovery. Never delete
; recursively or follow a reparse point, and never interrupt a running backend.
Var WanxiangPreviousSidecar
!include FileFunc.nsh

!macro WanxiangRejectLinkedParents path
  StrCpy $1 "${path}"
  ${Do}
    ; Capture last-error in the same System call; a subsequent plugin call can
    ; overwrite it before GetLastError is read.
    System::Call 'kernel32::GetFileAttributesW(w r1) i.r0 ?e'
    Pop $2
    ${If} $0 = -1
      ${If} $2 <> 2
      ${AndIf} $2 <> 3
        SetErrorLevel 2
        Abort "Could not verify the installation folder. No application files were replaced."
      ${EndIf}
    ${Else}
      IntOp $0 $0 & 0x400
      ${If} $0 <> 0
        SetErrorLevel 2
        Abort "Installation folders must not be junctions or symbolic links."
      ${EndIf}
    ${EndIf}
    ${GetParent} "$1" $2
    ${IfThen} $1 == $2 ${|} ${Break} ${|}
    StrCpy $1 $2
  ${LoopUntil} $1 == ""
!macroend

!macro NSIS_HOOK_PREINSTALL
  StrCpy $WanxiangPreviousSidecar ""
  ; These guards also apply to new/empty targets and script-managed upgrades.
  !insertmacro WanxiangRejectLinkedParents "$INSTDIR\sidecar\wanxiang-api"
  nsis_tauri_utils::FindProcessCurrentUser "${MAINBINARYNAME}.exe"
  Pop $0
  ${If} $0 <> 1
    SetErrorLevel 2
    Abort "Close Wanxiang Chengwen before updating. Your running tasks have not been interrupted."
  ${EndIf}
  nsis_tauri_utils::FindProcessCurrentUser "wanxiang-api.exe"
  Pop $0
  ${If} $0 <> 1
    SetErrorLevel 2
    Abort "The local backend may still be running. Close the application and wait before updating."
  ${EndIf}
  ${If} ${FileExists} "$INSTDIR\sidecar\wanxiang-api\*.*"
    ${IfNot} ${FileExists} "$INSTDIR\${MAINBINARYNAME}.exe"
    ${OrIfNot} ${FileExists} "$INSTDIR\sidecar\wanxiang-api\wanxiang-api.exe"
      SetErrorLevel 2
      Abort "Installation folder contains an unrecognized runtime. Choose another folder."
    ${EndIf}
    System::Call 'kernel32::GetTickCount64() l.r0'
    StrCpy $WanxiangPreviousSidecar "$INSTDIR\sidecar\wanxiang-api.rollback-${VERSION}-$0"
    ${If} ${FileExists} "$WanxiangPreviousSidecar\*.*"
      SetErrorLevel 2
      Abort "A recovery folder already exists. No installation files were changed."
    ${EndIf}
    ClearErrors
    Rename "$INSTDIR\sidecar\wanxiang-api" "$WanxiangPreviousSidecar"
    ${If} ${Errors}
      SetErrorLevel 2
      Abort "Could not preserve the previous runtime. No application files were replaced."
    ${EndIf}
    ClearErrors
    CopyFiles /SILENT "$INSTDIR\${MAINBINARYNAME}.exe" "$WanxiangPreviousSidecar\wanxiang-chengwen.previous.exe"
    ${If} ${Errors}
      Rename "$WanxiangPreviousSidecar" "$INSTDIR\sidecar\wanxiang-api"
      SetErrorLevel 2
      Abort "Could not preserve the previous application. Update stopped."
    ${EndIf}
    DetailPrint "Previous runtime preserved at $WanxiangPreviousSidecar"
  ${EndIf}
!macroend

!macro NSIS_HOOK_POSTINSTALL
  ${If} $WanxiangPreviousSidecar != ""
    FileOpen $0 "$INSTDIR\last-runtime-backup.txt" w
    FileWrite $0 "$WanxiangPreviousSidecar$\r$\n"
    FileClose $0
  ${EndIf}
!macroend
