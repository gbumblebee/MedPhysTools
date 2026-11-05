@echo off
rem Solution de secours si le service bloque les fichiers .vbs : une fenetre noire
rem apparait un court instant, puis MedPhysNotebook tourne seul.
cd /d "%~dp0..\.."
if exist "python\pythonw.exe" (
    start "" "python\pythonw.exe" -m medphystools.interface.application
) else if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" -m medphystools.interface.application
) else (
    echo Python est introuvable. Dans ce dossier, lancer une fois : uv sync
    pause
)
