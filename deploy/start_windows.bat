@echo off
REM Dauerbetrieb unter Windows. Über die Aufgabenplanung "Bei Anmeldung" starten lassen.
cd /d %~dp0\..
:loop
.venv\Scripts\python.exe -m goldsignal loop
echo Programm beendet - Neustart in 30 Sekunden ...
timeout /t 30
goto loop
