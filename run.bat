@echo off
rem Start Live Translator from this folder, with no console window.
cd /d "%~dp0"
start "" pythonw -m livetranslator %*
