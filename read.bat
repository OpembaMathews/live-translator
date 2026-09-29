@echo off
rem Read a paper aloud. Pass a PDF path to open it straight away.
cd /d "%~dp0"
start "" pythonw -m livetranslator.ui.reader_window %*
