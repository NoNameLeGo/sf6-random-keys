@echo off
setlocal
rem SF6 Random Keys - virtual gamepad edition launcher (full panel)
rem Opens the full control panel (start/stop buttons + parameters).
rem   F9 = start   /   F10 = stop   /   F8 = taunt
rem
rem Interpreter picking: prefer the known-good Python, and require BOTH tkinter
rem and the vgamepad PACKAGE. find_spec() only checks that the package is
rem installed - it does NOT connect to the ViGEmBus driver, so a broken driver
rem is still reported later by the program itself with a clear dialog.
set "ARGS=sf6_gamepad.pyw"
set "CHK=import tkinter, importlib.util, sys; sys.exit(0 if importlib.util.find_spec('vgamepad') else 1)"
cd /d "%~dp0"

set "KNOWN=%LOCALAPPDATA%\Python\pythoncore-3.14-64\pythonw.exe"
if exist "%KNOWN%" (
  "%KNOWN%" -c "%CHK%" >nul 2>nul
  if not errorlevel 1 (
    start "" "%KNOWN%" %ARGS%
    exit /b 0
  )
)

where pyw >nul 2>nul
if errorlevel 1 goto try_pythonw
py -c "%CHK%" >nul 2>nul
if not errorlevel 1 goto use_pyw

:try_pythonw
where pythonw >nul 2>nul
if errorlevel 1 goto try_store
python -c "%CHK%" >nul 2>nul
if not errorlevel 1 goto use_pythonw

:try_store
if not exist "%LOCALAPPDATA%\Microsoft\WindowsApps\pythonw.exe" goto no_python
"%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe" -c "%CHK%" >nul 2>nul
if not errorlevel 1 goto use_store

:no_python
echo [ERROR] No Python with BOTH tkinter and the vgamepad package was found.
echo   - Install a full Python 3.8+ build, then run:  pip install vgamepad
echo   - Or start the program manually:  py sf6_gamepad.pyw
echo.
pause
exit /b 1

:use_pyw
start "" pyw %ARGS%
exit /b 0

:use_pythonw
start "" pythonw %ARGS%
exit /b 0

:use_store
start "" "%LOCALAPPDATA%\Microsoft\WindowsApps\pythonw.exe" %ARGS%
exit /b 0
