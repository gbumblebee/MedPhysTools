' Cree sur le Bureau un raccourci vers MedPhysNotebook, avec son logo.
Option Explicit

Dim fichiers, coquille, dossier, racine, raccourci

Set fichiers = CreateObject("Scripting.FileSystemObject")
Set coquille = CreateObject("WScript.Shell")

dossier = fichiers.GetParentFolderName(WScript.ScriptFullName)
racine = fichiers.GetParentFolderName(fichiers.GetParentFolderName(dossier))

Set raccourci = coquille.CreateShortcut( _
    coquille.SpecialFolders("Desktop") & "\medphysnotebook.lnk")
raccourci.TargetPath = fichiers.GetSpecialFolder(1).Path & "\wscript.exe"
raccourci.Arguments = """" & dossier & "\medphysnotebook.vbs"""
raccourci.WorkingDirectory = racine
raccourci.IconLocation = racine & "\src\medphystools\interface\ressources\logo.ico,0"
raccourci.Description = "MedPhysNotebook : cahier de laboratoire du physicien medical"
raccourci.Save

MsgBox "Le raccourci MedPhysNotebook est sur le Bureau.", vbInformation, "MedPhysNotebook"
