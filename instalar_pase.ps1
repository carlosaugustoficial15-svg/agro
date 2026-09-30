$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object Windows.Forms.Form
$form.Text = 'Instalacao do PASE Studio'
$form.Size = New-Object Drawing.Size(600, 220)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false

$heading = New-Object Windows.Forms.Label
$heading.Text = 'Instalando o PASE Studio'
$heading.Font = New-Object Drawing.Font('Segoe UI', 16, [Drawing.FontStyle]::Bold)
$heading.SetBounds(25, 20, 540, 34)
$form.Controls.Add($heading)

$status = New-Object Windows.Forms.Label
$status.Text = 'Verificando este computador...'
$status.Font = New-Object Drawing.Font('Segoe UI', 10)
$status.SetBounds(28, 68, 535, 25)
$form.Controls.Add($status)

$progress = New-Object Windows.Forms.ProgressBar
$progress.Style = 'Marquee'
$progress.MarqueeAnimationSpeed = 22
$progress.SetBounds(28, 105, 535, 24)
$form.Controls.Add($progress)

$detail = New-Object Windows.Forms.Label
$detail.Text = 'A instalacao pode levar varios minutos e baixar mais de 1 GB.'
$detail.ForeColor = [Drawing.Color]::DimGray
$detail.SetBounds(28, 144, 535, 24)
$form.Controls.Add($detail)

function Set-Status([string]$message, [string]$extra = '') {
    $status.Text = $message
    if ($extra) { $detail.Text = $extra }
    $form.Refresh()
    [Windows.Forms.Application]::DoEvents()
}

function Get-File([string]$url, [string]$destination, [string]$description) {
    Set-Status $description 'Baixando do servidor oficial; mantenha esta janela aberta.'
    $client = New-Object Net.WebClient
    try {
        $task = $client.DownloadFileTaskAsync([Uri]$url, $destination)
        while (-not $task.IsCompleted) {
            [Windows.Forms.Application]::DoEvents()
            Start-Sleep -Milliseconds 150
        }
        $task.GetAwaiter().GetResult()
    } finally {
        $client.Dispose()
    }
}

function Invoke-Hidden([string]$file, [string]$arguments, [string]$workingDirectory, [string]$description) {
    Set-Status $description 'Preparando dependencias. Isso pode demorar alguns minutos.'
    $process = Start-Process -FilePath $file -ArgumentList $arguments -WorkingDirectory $workingDirectory -WindowStyle Hidden -PassThru
    while (-not $process.HasExited) {
        [Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 200
        $process.Refresh()
    }
    if ($process.ExitCode -ne 0) { throw "$description falhou (codigo $($process.ExitCode))." }
}

function Get-LatestAsset([string]$apiUrl, [string]$assetName) {
    $release = Invoke-RestMethod -Uri $apiUrl -Headers @{ 'User-Agent' = 'PASE-Studio-Installer' }
    $asset = $release.assets | Where-Object { $_.name -eq $assetName } | Select-Object -First 1
    if (-not $asset) { throw "O instalador oficial nao forneceu o arquivo esperado: $assetName" }
    return $asset.browser_download_url
}

function Get-PortableGit([string]$tools) {
    $gitExe = Join-Path $tools 'PortableGit\cmd\git.exe'
    if (Test-Path $gitExe) { return $gitExe }
    $release = Invoke-RestMethod -Uri 'https://api.github.com/repos/git-for-windows/git/releases/latest' -Headers @{ 'User-Agent' = 'PASE-Studio-Installer' }
    $asset = $release.assets | Where-Object { $_.name -match '^PortableGit-.*-64-bit\.7z\.exe$' } | Select-Object -First 1
    if (-not $asset) { throw 'Nao foi possivel localizar a versao portatil oficial do Git para Windows.' }
    $installer = Join-Path $env:TEMP $asset.name
    Get-File $asset.browser_download_url $installer 'Baixando Git para clonar o repositorio...'
    Set-Status 'Instalando Git portatil...' 'Instalacao por usuario; nao requer administrador.'
    $extractArgs = "-o`"$tools`" -y"
    Invoke-Hidden $installer $extractArgs $env:TEMP 'Instalacao do Git'
    Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path $gitExe)) { throw 'O Git terminou a instalacao, mas git.exe nao foi encontrado.' }
    return $gitExe
}

function Get-Miniforge([string]$tools) {
    $root = Join-Path $tools 'Miniforge3'
    $conda = Join-Path $root 'Scripts\conda.exe'
    if (Test-Path $conda) { return $conda }
    $url = Get-LatestAsset 'https://api.github.com/repos/conda-forge/miniforge/releases/latest' 'Miniforge3-Windows-x86_64.exe'
    $installer = Join-Path $env:TEMP 'Miniforge3-Windows-x86_64.exe'
    Get-File $url $installer 'Baixando Miniforge (Python e ambiente cientifico)...'
    Set-Status 'Instalando Python/Conda...' 'Instalacao silenciosa no perfil do usuario; nao requer administrador.'
    $args = "/InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /S /D=`"$root`""
    Invoke-Hidden $installer $args $env:TEMP 'Instalacao do Miniforge'
    Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path $conda)) { throw 'Miniforge foi instalado, mas conda.exe nao foi encontrado.' }
    return $conda
}

function Get-Node([string]$tools) {
    $index = Invoke-RestMethod -Uri 'https://nodejs.org/dist/index.json'
    $release = $index | Where-Object { $_.lts } | Select-Object -First 1
    if (-not $release) { throw 'Nao foi possivel localizar uma versao LTS do Node.js.' }
    $version = $release.version.TrimStart('v')
    $root = Join-Path $tools "node-v$version-win-x64"
    $node = Join-Path $root 'node.exe'
    if (Test-Path $node) { return @{ Root = $root; Node = $node } }
    $archive = Join-Path $env:TEMP "node-v$version-win-x64.zip"
    Get-File "https://nodejs.org/dist/v$version/node-v$version-win-x64.zip" $archive 'Baixando Node.js LTS para montar a interface...'
    Set-Status 'Instalando Node.js...' 'Preparando o compilador da interface desktop.'
    Expand-Archive -LiteralPath $archive -DestinationPath $tools -Force
    Remove-Item -LiteralPath $archive -Force -ErrorAction SilentlyContinue
    if (-not (Test-Path $node)) { throw 'O arquivo node.exe nao foi encontrado apos a instalacao.' }
    return @{ Root = $root; Node = $node }
}

function Write-DesktopLauncher([string]$repo) {
    $desktop = [Environment]::GetFolderPath('DesktopDirectory')
    $launcher = Join-Path $desktop 'PASE Studio.bat'
    $escaped = $repo.Replace('%', '%%')
    $content = @"
@echo off
setlocal
set "PASE_ROOT=$escaped"
if not exist "%PASE_ROOT%\Abrir PASE.vbs" (
  powershell.exe -NoLogo -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('A pasta do PASE nao foi encontrada. Execute novamente Instalar PASE.bat.', 'PASE Studio') | Out-Null"
  exit /b 1
)
start "" wscript.exe "%PASE_ROOT%\Abrir PASE.vbs"
exit /b 0
"@
    [IO.File]::WriteAllText($launcher, $content, [Text.UTF8Encoding]::new($false))
    return $launcher
}

$form.Add_Shown({
    try {
        if (-not [Environment]::Is64BitOperatingSystem) { throw 'O PASE Studio requer Windows 64 bits.' }
        if ([Environment]::OSVersion.Version.Major -lt 10) { throw 'O PASE Studio requer Windows 10 ou superior.' }

        $tools = Join-Path $env:LOCALAPPDATA 'PASE\tools'
        New-Item -ItemType Directory -Path $tools -Force | Out-Null
        $git = Get-PortableGit $tools

        $parent = Join-Path $env:USERPROFILE 'PASE'
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        $repo = Join-Path $parent 'agro'
        if (Test-Path $repo) {
            $suffix = Get-Date -Format 'yyyyMMdd-HHmmss'
            $repo = Join-Path $parent "agro-$suffix"
        }
        Set-Status 'Clonando PASE Studio e o modelo pySTICS...' 'Baixando os arquivos do GitHub e o submodulo cientifico.'
        $cloneArgs = "clone --depth 1 --recurse-submodules --shallow-submodules https://github.com/carlosaugustoficial15-svg/agro.git `"$repo`""
        Invoke-Hidden $git $cloneArgs $parent 'Clone do PASE no GitHub'

        $conda = Get-Miniforge $tools
        $environment = Join-Path $repo 'environment_windows.yml'
        $envCheckArgs = "run -n pase-2026-07 python --version"
        $check = Start-Process -FilePath $conda -ArgumentList $envCheckArgs -WorkingDirectory $repo -WindowStyle Hidden -PassThru -Wait
        if ($check.ExitCode -ne 0) {
            Invoke-Hidden $conda "env create -f `"$environment`"" $repo 'Instalacao das bibliotecas cientificas PASE'
        }

        $nodeInfo = Get-Node $tools
        $studio = Join-Path $repo 'studio'
        $npmCli = Join-Path $nodeInfo.Root 'node_modules\npm\bin\npm-cli.js'
        $nodePath = Join-Path $nodeInfo.Root 'node.exe'
        Invoke-Hidden $nodePath "`"$npmCli`" ci --no-audit --no-fund" $studio 'Instalacao da interface e do Electron'
        Invoke-Hidden $nodePath "`"$npmCli`" run build" $studio 'Compilacao do PASE Studio'

        $desktopLauncher = Write-DesktopLauncher $repo
        Set-Status 'Instalacao concluida!' "Atalho criado na Area de Trabalho: $desktopLauncher"
        $progress.Style = 'Continuous'
        $progress.Value = 100
        [Windows.Forms.MessageBox]::Show("PASE Studio pronto.`r`n`r`nAtalho criado na Area de Trabalho.`r`n`r`nPasta: $repo", 'PASE Studio', 'OK', 'Information') | Out-Null
        $form.Close()
    } catch {
        $progress.Style = 'Continuous'
        $progress.Value = 0
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, 'PASE - Falha na instalacao', 'OK', 'Error') | Out-Null
        $form.Close()
    }
})

[void]$form.ShowDialog()
