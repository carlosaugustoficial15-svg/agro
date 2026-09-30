Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$envFile = Join-Path $root 'environment_windows.yml'
$envName = 'pase-2026-07'
$installDir = Join-Path $env:LOCALAPPDATA 'Miniforge3'

$form = New-Object Windows.Forms.Form
$form.Text = 'PASE - Inicialização'
$form.Size = New-Object Drawing.Size(560, 205)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false

$title = New-Object Windows.Forms.Label
$title.Text = 'Preparando o PASE'
$title.Font = New-Object Drawing.Font('Segoe UI', 16, [Drawing.FontStyle]::Bold)
$title.SetBounds(25, 20, 500, 35)
$form.Controls.Add($title)

$status = New-Object Windows.Forms.Label
$status.Text = 'Verificando os componentes necessários...'
$status.Font = New-Object Drawing.Font('Segoe UI', 10)
$status.SetBounds(28, 67, 490, 24)
$form.Controls.Add($status)

$progress = New-Object Windows.Forms.ProgressBar
$progress.Style = 'Marquee'
$progress.MarqueeAnimationSpeed = 25
$progress.SetBounds(28, 100, 490, 24)
$form.Controls.Add($progress)

$detail = New-Object Windows.Forms.Label
$detail.Text = 'A primeira preparação pode levar alguns minutos.'
$detail.ForeColor = [Drawing.Color]::DimGray
$detail.SetBounds(28, 134, 490, 20)
$form.Controls.Add($detail)

function Update-Status([string]$text) {
    $status.Text = $text
    $form.Refresh()
    [Windows.Forms.Application]::DoEvents()
}

function Find-Conda {
    $candidates = @(
        (Join-Path $installDir 'condabin\conda.bat'),
        (Join-Path $env:USERPROFILE 'miniforge3\condabin\conda.bat'),
        (Join-Path $env:USERPROFILE 'miniconda3\condabin\conda.bat'),
        (Join-Path $env:USERPROFILE 'anaconda3\condabin\conda.bat'),
        (Join-Path $env:ProgramData 'Miniconda3\condabin\conda.bat'),
        (Join-Path $env:ProgramData 'Anaconda3\condabin\conda.bat')
    )
    foreach ($candidate in $candidates) { if (Test-Path $candidate) { return $candidate } }
    return $null
}

function Run-Hidden([string]$command, [string]$arguments) {
    $process = Start-Process -FilePath $command -ArgumentList $arguments -WindowStyle Hidden -PassThru
    while (-not $process.HasExited) {
        [Windows.Forms.Application]::DoEvents()
        Start-Sleep -Milliseconds 150
        $process.Refresh()
    }
    return $process.ExitCode
}

$form.Add_Shown({
    try {
        $conda = Find-Conda
        if (-not $conda) {
            Update-Status 'Baixando o Miniforge oficial...'
            $installer = Join-Path $env:TEMP 'Miniforge3-Windows-x86_64.exe'
            $url = 'https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Windows-x86_64.exe'
            (New-Object Net.WebClient).DownloadFile($url, $installer)
            Update-Status 'Instalando o Miniforge para o seu usuário...'
            $code = Run-Hidden $installer "/InstallationType=JustMe /RegisterPython=0 /AddToPath=0 /S /D=$installDir"
            Remove-Item $installer -Force -ErrorAction SilentlyContinue
            if ($code -ne 0) { throw "A instalação do Miniforge falhou (código $code)." }
            $conda = Find-Conda
        }
        if (-not $conda) { throw 'O Conda não foi encontrado após a instalação.' }

        Update-Status 'Verificando o ambiente de simulação...'
        $check = Run-Hidden 'cmd.exe' "/d /c `"`"$conda`" run -n $envName python --version`""
        if ($check -ne 0) {
            Update-Status 'Criando o ambiente do PASE e instalando dependências...'
            $create = Run-Hidden 'cmd.exe' "/d /c `"`"$conda`" env create -f `"$envFile`"`""
            if ($create -ne 0) { throw "Não foi possível criar o ambiente (código $create)." }
        }

        Update-Status 'Preparando o editor visual moderno...'
        $studio = Join-Path $root 'studio'
        $electron = Join-Path $studio 'node_modules\electron\dist\electron.exe'
        if (-not (Test-Path $electron)) {
            $npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
            if (-not $npm) { throw 'Node.js não foi encontrado. Instale-o em https://nodejs.org/' }
            $install = Run-Hidden 'cmd.exe' "/d /c cd /d `"$studio`" && `"$npm`" install"
            if ($install -ne 0) { throw "Não foi possível instalar a interface visual (código $install)." }
            if (-not (Test-Path $electron)) {
                $node = (Get-Command node.exe -ErrorAction SilentlyContinue).Source
                if (-not $node) { throw 'Node.js foi encontrado sem o executável node.exe.' }
                $electronInstaller = Join-Path $studio 'node_modules\electron\install.js'
                $electronInstall = Run-Hidden $node "`"$electronInstaller`""
                if ($electronInstall -ne 0 -or -not (Test-Path $electron)) {
                    throw 'Não foi possível baixar o runtime visual do Electron.'
                }
            }
        }
        Update-Status 'Abrindo o PASE Studio...'
        $condaBase = Split-Path -Parent (Split-Path -Parent $conda)
        $python = Join-Path $condaBase "envs\$envName\python.exe"
        $env:PASE_PYTHON = $python
        Start-Process -FilePath $electron -ArgumentList "`"$studio`"" -WorkingDirectory $studio
        $form.Close()
    } catch {
        $progress.Style = 'Continuous'
        $progress.Value = 0
        [Windows.Forms.MessageBox]::Show($_.Exception.Message, 'PASE - Erro', 'OK', 'Error') | Out-Null
        $form.Close()
    }
})

[void]$form.ShowDialog()
