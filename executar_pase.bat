@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem Always run from the repository directory, even when launched by double-click.
cd /d "%~dp0"

set "ENV_FILE=%CD%\environment_windows.yml"
set "ENV_NAME=pase-2026-07"
set "RUN_TARGET=example.py"
set "SETUP_ONLY=0"
set "NO_PAUSE=0"

:parse_arguments
if "%~1"=="" goto arguments_done
if /I "%~1"=="--all" set "RUN_TARGET=run_all_example_scripts.py"& shift& goto parse_arguments
if /I "%~1"=="--setup-only" set "SETUP_ONLY=1"& shift& goto parse_arguments
if /I "%~1"=="--no-pause" set "NO_PAUSE=1"& shift& goto parse_arguments
if /I "%~1"=="--help" goto help
echo [ERRO] Opcao desconhecida: %~1
goto usage_error

:arguments_done
echo ============================================================
echo                    PASE - EXECUCAO FACIL
echo ============================================================
echo.
call :progress 5 "Verificando os arquivos do PASE"

if not exist "%ENV_FILE%" (
    echo [ERRO] O arquivo environment_windows.yml nao foi encontrado.
    echo        Execute este arquivo dentro da pasta raiz do PASE.
    goto failure
)

call :find_conda
if not defined CONDA_CMD (
    call :progress 10 "Preparando a instalacao do Miniforge"
    echo [INFO] Conda nao foi encontrado.
    echo.
    echo [INFO] O Miniforge sera instalado automaticamente somente para
    echo        o seu usuario. Nenhuma permissao de administrador e necessaria.
    call :install_miniforge
    if errorlevel 1 goto failure
)

call :progress 35 "Conda disponivel"
echo [OK] Conda encontrado: %CONDA_CMD%
echo [INFO] Ambiente: %ENV_NAME%

call "%CONDA_CMD%" run -n "%ENV_NAME%" python --version >nul 2>&1
if errorlevel 1 (
    call :progress 40 "Criando o ambiente Conda"
    echo.
    echo [INFO] Primeira execucao: criando o ambiente. Isso pode demorar.
    call "%CONDA_CMD%" env create -f "%ENV_FILE%"
    if errorlevel 1 (
        echo.
        echo [ERRO] Nao foi possivel criar o ambiente Conda.
        goto failure
    )
) else (
    echo [OK] O ambiente Conda ja esta pronto.
)

call :progress 80 "Ambiente pronto"

if "%SETUP_ONLY%"=="1" (
    echo.
    echo [OK] Preparacao concluida. Nenhuma simulacao foi iniciada.
    goto success
)

if not exist "%RUN_TARGET%" (
    echo [ERRO] Script de execucao nao encontrado: %RUN_TARGET%
    goto failure
)

echo.
echo [INFO] Iniciando: %RUN_TARGET%
echo [INFO] Mantenha esta janela aberta durante a simulacao.
echo.
call :progress 85 "Executando a simulacao"
call "%CONDA_CMD%" run --no-capture-output -n "%ENV_NAME%" python "%RUN_TARGET%"
if errorlevel 1 (
    echo.
    echo [ERRO] A simulacao terminou com erro.
    goto failure
)

echo.
call :progress 100 "Concluido"
echo [OK] Simulacao concluida com sucesso.
goto success

:find_conda
set "CONDA_CMD="
for /f "delims=" %%C in ('where conda.bat 2^>nul') do if not defined CONDA_CMD set "CONDA_CMD=%%C"
if defined CONDA_CMD exit /b 0

for %%C in (
    "%USERPROFILE%\miniforge3\condabin\conda.bat"
    "%USERPROFILE%\Miniforge3\condabin\conda.bat"
    "%USERPROFILE%\miniconda3\condabin\conda.bat"
    "%USERPROFILE%\Miniconda3\condabin\conda.bat"
    "%USERPROFILE%\anaconda3\condabin\conda.bat"
    "%USERPROFILE%\Anaconda3\condabin\conda.bat"
    "%LOCALAPPDATA%\miniforge3\condabin\conda.bat"
    "%LOCALAPPDATA%\miniconda3\condabin\conda.bat"
    "%ProgramData%\miniforge3\condabin\conda.bat"
    "%ProgramData%\Miniconda3\condabin\conda.bat"
    "%ProgramData%\Anaconda3\condabin\conda.bat"
) do if not defined CONDA_CMD if exist "%%~C" set "CONDA_CMD=%%~C"
exit /b 0

:install_miniforge
set "MINIFORGE_DIR=%LOCALAPPDATA%\Miniforge3"
set "MINIFORGE_INSTALLER=%TEMP%\Miniforge3-Windows-x86_64.exe"
set "MINIFORGE_URL=https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe"

echo [INFO] Baixando o instalador oficial do Miniforge...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; Invoke-WebRequest -UseBasicParsing -Uri '%MINIFORGE_URL%' -OutFile '%MINIFORGE_INSTALLER%'"
if errorlevel 1 (
    echo [ERRO] Nao foi possivel baixar o Miniforge.
    echo        Verifique a conexao com a internet ou instale manualmente:
    echo        https://github.com/conda-forge/miniforge
    exit /b 1
)

echo [INFO] Instalando o Miniforge em: %MINIFORGE_DIR%
"%MINIFORGE_INSTALLER%" /InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /S /D=%MINIFORGE_DIR%
if errorlevel 1 (
    echo [ERRO] A instalacao do Miniforge falhou.
    exit /b 1
)

set "CONDA_CMD=%MINIFORGE_DIR%\condabin\conda.bat"
if not exist "%CONDA_CMD%" (
    echo [ERRO] A instalacao terminou, mas conda.bat nao foi encontrado.
    exit /b 1
)

del /q "%MINIFORGE_INSTALLER%" >nul 2>&1
echo [OK] Miniforge instalado com sucesso.
exit /b 0

:progress
set /a "PROGRESS_VALUE=%~1"
set /a "PROGRESS_FILLED=PROGRESS_VALUE/5"
set /a "PROGRESS_EMPTY=20-PROGRESS_FILLED"
set "PROGRESS_BAR="
for /l %%B in (1,1,!PROGRESS_FILLED!) do set "PROGRESS_BAR=!PROGRESS_BAR!#"
for /l %%B in (1,1,!PROGRESS_EMPTY!) do set "PROGRESS_BAR=!PROGRESS_BAR!-"
echo [!PROGRESS_BAR!] !PROGRESS_VALUE!%% - %~2
exit /b 0

:help
echo Uso: executar_pase.bat [opcao]
echo.
echo Sem opcao       Instala/prepara o ambiente, se necessario, e executa example.py.
echo --all           Executa a sequencia de exemplos automatizados.
echo --setup-only    Apenas cria/verifica o ambiente Conda.
echo --no-pause      Nao aguarda uma tecla antes de fechar.
echo --help          Exibe esta ajuda.
exit /b 0

:usage_error
echo Use executar_pase.bat --help para ver as opcoes disponiveis.
goto failure

:success
set "EXIT_CODE=0"
goto finish

:failure
set "EXIT_CODE=1"

:finish
echo.
if not "%NO_PAUSE%"=="1" pause
exit /b %EXIT_CODE%
