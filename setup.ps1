# First-time setup: create the virtual environment, install packages, create .env
# Run from the project folder: powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Pick Python 3.10+. Plain "python" can be an older install that comes first on PATH,
# so try the Windows py launcher with each supported version first.
$pythonCmd = $null
foreach ($v in @("3.12", "3.11", "3.13", "3.10")) {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        & py "-$v" -c "import sys" 2>$null
        if ($LASTEXITCODE -eq 0) { $pythonCmd = @("py", "-$v"); break }
    }
}
if (-not $pythonCmd -and (Get-Command python -ErrorAction SilentlyContinue)) {
    & python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
    if ($LASTEXITCODE -eq 0) { $pythonCmd = @("python") }
}
if (-not $pythonCmd) {
    Write-Host "Python 3.10+ not found. Install Python 3.12 from python.org (tick 'Add python.exe to PATH')" -ForegroundColor Red
    exit 1
}

if (Test-Path ".venv") {
    & .venv\Scripts\python.exe -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "The existing .venv uses Python older than 3.10. Delete the .venv folder and run setup again." -ForegroundColor Red
        exit 1
    }
} else {
    Write-Host "Creating virtual environment with $($pythonCmd -join ' ')..."
    $exe = $pythonCmd[0]
    $launcherArgs = @($pythonCmd | Select-Object -Skip 1)
    & $exe @launcherArgs -m venv .venv
}

Write-Host "Installing packages..."
& .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host "Created .env. Open it in Notepad and paste your API key." -ForegroundColor Yellow
} else {
    Write-Host ".env already exists, skipping"
}

Write-Host ""
Write-Host "Setup done! Every time you start work:" -ForegroundColor Green
Write-Host "  .venv\Scripts\Activate.ps1"
Write-Host "  streamlit run app.py"
