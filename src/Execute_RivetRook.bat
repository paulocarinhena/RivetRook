@echo off
setlocal enabledelayedexpansion

python --version >nul 2>&1
if %ERRORLEVEL% equ 0 goto :run

py --version >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PY_CMD=py"
    goto :run
)

echo.
echo  [!] Python nao foi encontrado no sistema.
echo      Python e um pre-requisito para executar o RivetRook.
echo.
set /p "INSTALL=  Deseja instalar o Python 3.11 agora via winget? (S/N): "
if /i "!INSTALL!"=="S" goto :install
if /i "!INSTALL!"=="Y" goto :install

echo.
echo  [X] Instalacao cancelada. Encerrando.
echo.
pause
exit /b 1

:install
where winget >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo.
    echo  [X] winget nao esta disponivel neste sistema.
    echo      Instale o "App Installer" pela Microsoft Store e tente novamente,
    echo      ou instale o Python manualmente em https://www.python.org/downloads/
    echo.
    pause
    exit /b 1
)

echo.
echo  [*] Instalando Python 3.11 via winget...
echo.
winget install -e --id Python.Python.3.11
if %ERRORLEVEL% neq 0 (
    echo.
    echo  [X] Falha ao instalar o Python via winget.
    echo.
    pause
    exit /b 1
)

echo.
echo  [OK] Python instalado com sucesso.
echo       Feche este terminal e abra novamente para que o PATH seja atualizado,
echo       em seguida execute este arquivo (Execute_RivetRook.bat) novamente.
echo.
pause
exit /b 0

:run
if not exist "%~dp0RivetRook.py" (
    echo.
    echo  [X] Arquivo nao encontrado: "%~dp0RivetRook.py"
    echo.
    pause
    exit /b 1
)

if not defined PY_CMD set "PY_CMD=python"

REM ── Se Git esta instalado mas nao no PATH, adicionar para esta sessao ──
where git >nul 2>&1
if %ERRORLEVEL% neq 0 (
    if exist "C:\Program Files\Git\bin\git.exe" (
        set "PATH=C:\Program Files\Git\bin;!PATH!"
    ) else if exist "C:\Program Files (x86)\Git\bin\git.exe" (
        set "PATH=C:\Program Files (x86)\Git\bin;!PATH!"
    )
)

set "PS_EXE=powershell"
where pwsh >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PS_EXE=pwsh"
    goto :launch
)

echo.
echo  [!] PowerShell 7+ (pwsh) nao foi encontrado.
echo      O RivetRook requer o PowerShell 7+ para funcionar corretamente.
echo.
set /p "INSTALL_PS=  Deseja instalar o PowerShell 7.6.0 agora? (S/N): "
if /i "!INSTALL_PS!"=="S" goto :install_pwsh
if /i "!INSTALL_PS!"=="Y" goto :install_pwsh
goto :pwsh_fail

:install_pwsh
echo.
echo  [*] Baixando PowerShell 7.6.0...
curl -L -o "%TEMP%\PowerShell-7.6.0-win-x64.msi" "https://github.com/PowerShell/PowerShell/releases/download/v7.6.0/PowerShell-7.6.0-win-x64.msi"
if %ERRORLEVEL% neq 0 (
    echo.
    echo  [X] Falha ao baixar o PowerShell 7.6.0.
    goto :pwsh_fail
)

echo  [*] Instalando PowerShell 7.6.0 (pode pedir permissao de administrador)...
msiexec /i "%TEMP%\PowerShell-7.6.0-win-x64.msi" /quiet ADD_EXPLORER_CONTEXT_MENU_OPENPOWERSHELL=1 ADD_FILE_CONTEXT_MENU_RUNPOWERSHELL=1 ENABLE_PSREMOTING=0 REGISTER_MANIFEST=1 USE_MU=1 ENABLE_MU=1 ADD_PATH=1
if %ERRORLEVEL% neq 0 (
    echo.
    echo  [X] Falha ao instalar o PowerShell 7.6.0.
    goto :pwsh_fail
)

REM Atualizar PATH no processo atual para encontrar pwsh sem reabrir terminal
for /f "tokens=2*" %%A in ('reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v Path 2^>nul') do set "SYS_PATH=%%B"
for /f "tokens=2*" %%A in ('reg query "HKCU\Environment" /v Path 2^>nul') do set "USR_PATH=%%B"
set "PATH=!SYS_PATH!;!USR_PATH!"

where pwsh >nul 2>&1
if %ERRORLEVEL% equ 0 (
    set "PS_EXE=pwsh"
    echo.
    echo  [OK] PowerShell 7.6.0 instalado com sucesso.
    echo.
    goto :launch
)

:pwsh_fail
echo.
echo  [X] Nao foi possivel instalar o PowerShell 7+ automaticamente.
echo      Baixe e instale manualmente:
echo.
echo      https://github.com/PowerShell/PowerShell/releases/download/v7.6.0/PowerShell-7.6.0-win-x64.msi
echo.
echo      Apos instalar, feche e reabra o terminal, e execute este arquivo novamente.
echo.
pause
exit /b 1

:launch
start "RivetRook" %PS_EXE% -NoProfile -ExecutionPolicy Bypass -NoExit -Command "& { %PY_CMD% '%~dp0RivetRook.py' }"
exit /b 0
