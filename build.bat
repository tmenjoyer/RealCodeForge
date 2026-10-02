@echo off
setlocal
cd /d "%~dp0"

REM ---- Find a working Python (python.exe first, then the "py" launcher) ----
set "PY="
python --version >nul 2>nul && set "PY=python"
if not defined PY py -3 --version >nul 2>nul && set "PY=py -3"
if not defined PY (
    echo Python was not found.
    echo Install it from https://www.python.org/downloads/ and tick "Add python.exe to PATH".
    goto :error
)

echo Using: %PY%
echo.
echo [1/3] Installing dependencies...
%PY% -m pip install --upgrade pip
%PY% -m pip install -r requirements.txt -r requirements-build.txt
if errorlevel 1 goto :error

echo.
echo [2/3] Building CodeForge.exe...
%PY% -m PyInstaller --noconfirm --onefile --windowed --name CodeForge --collect-submodules pygments codeforge.py
if errorlevel 1 goto :error

echo.
echo [3/3] Creating CodeForge-windows-x64.zip...
if exist package rmdir /s /q package
mkdir package
copy /Y dist\CodeForge.exe package\ >nul
copy /Y README.md package\ >nul
copy /Y LICENSE package\ >nul
if exist CodeForge-windows-x64.zip del CodeForge-windows-x64.zip
powershell -NoProfile -Command "Compress-Archive -Path 'package\*' -DestinationPath 'CodeForge-windows-x64.zip' -Force"
if errorlevel 1 goto :error

echo.
echo Done: CodeForge-windows-x64.zip
pause
exit /b 0

:error
echo.
echo Build failed. Read the messages above for the reason.
pause
exit /b 1
