' Cree sur le Bureau un raccourci vers pymphub, avec son logo.
Option Explicit

Dim fichiers, coquille, dossier, racine, raccourci

Set fichiers = CreateObject("Scripting.FileSystemObject")
Set coquille = CreateObject("WScript.Shell")

dossier = fichiers.GetParentFolderName(WScript.ScriptFullName)
racine = fichiers.GetParentFolderName(fichiers.GetParentFolderName(dossier))

Set raccourci = coquille.CreateShortcut( _
    coquille.SpecialFolders("Desktop") & "\pymphub.lnk")
raccourci.TargetPath = fichiers.GetSpecialFolder(1).Path & "\wscript.exe"
raccourci.Arguments = """" & dossier & "\pymphub.vbs"""
raccourci.WorkingDirectory = racine
raccourci.IconLocation = racine & "\src\pymphub\interface\ressources\logo.ico,0"
raccourci.Description = "pymphub : cahier de laboratoire du physicien medical"
raccourci.Save

MsgBox "Le raccourci pymphub est sur le Bureau.", vbInformation, "pymphub"
