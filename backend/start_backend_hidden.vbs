Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

dossierBackend = "C:\Users\hamdi\Documents\vinted-dashboard\backend"
dossierLogs = dossierBackend & "\logs"

If Not fso.FolderExists(dossierLogs) Then
    fso.CreateFolder(dossierLogs)
End If

WshShell.CurrentDirectory = dossierBackend

commande = "cmd /c ""set PYTHONIOENCODING=utf-8 && echo ---- Demarrage %DATE% %TIME% ---- >> logs\backend.log && " & _
           dossierBackend & "\venv\Scripts\python.exe main.py >> logs\backend.log 2>&1"""

' Le "0" cache totalement la fenêtre (aucune console visible, même un instant)
' Le "False" ne bloque pas le script VBS en attendant la fin (le backend tourne indéfiniment)
WshShell.Run commande, 0, False