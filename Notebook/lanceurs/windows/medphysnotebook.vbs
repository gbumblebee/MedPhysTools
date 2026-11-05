' Lance MedPhysNotebook d'un double clic, sans fenetre de commande.
' Le programme tourne dans une fenetre cachee ; ses messages vont dans un journal,
' qui est montre si le programme s'arrete sur une erreur.
Option Explicit

Dim fichiers, coquille, racine, python, journal, commande, code

Set fichiers = CreateObject("Scripting.FileSystemObject")
Set coquille = CreateObject("WScript.Shell")

' Ce fichier est dans lanceurs\windows : la racine du projet est deux dossiers plus haut.
racine = fichiers.GetParentFolderName(fichiers.GetParentFolderName( _
    fichiers.GetParentFolderName(WScript.ScriptFullName)))

If fichiers.FileExists(racine & "\python\python.exe") Then
    python = racine & "\python\python.exe"
ElseIf fichiers.FileExists(racine & "\.venv\Scripts\python.exe") Then
    python = racine & "\.venv\Scripts\python.exe"
Else
    MsgBox "Python est introuvable dans :" & vbCrLf & racine & vbCrLf & vbCrLf & _
        "Dans ce dossier, lancer une fois : uv sync", vbExclamation, "MedPhysNotebook"
    WScript.Quit 1
End If

journal = fichiers.GetSpecialFolder(2).Path & "\medphysnotebook_journal.txt"
coquille.CurrentDirectory = racine
commande = "cmd /c """"" & python & """ -m medphystools.interface.application > """ & _
    journal & """ 2>&1"""

' 0 : aucune fenetre de commande. True : attend la fin, pour pouvoir signaler une erreur.
code = coquille.Run(commande, 0, True)
If code <> 0 Then
    MsgBox "MedPhysNotebook s'est arrete sur une erreur (code " & code & ")." & vbCrLf & vbCrLf & _
        "Le detail est dans :" & vbCrLf & journal, vbCritical, "MedPhysNotebook"
End If
WScript.Quit code
