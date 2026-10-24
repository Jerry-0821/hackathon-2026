# 第一次设置：建虚拟环境、装依赖、建 .env
# 运行方法（在项目文件夹里）：powershell -ExecutionPolicy Bypass -File setup.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "找不到 python，先去 python.org 装 Python 3.10 以上（安装时勾选 Add to PATH）" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".venv")) {
    Write-Host "建虚拟环境..."
    python -m venv .venv
}

Write-Host "安装依赖..."
& .venv\Scripts\python.exe -m pip install --quiet --upgrade pip
& .venv\Scripts\python.exe -m pip install --quiet -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host "已建好 .env，用记事本打开它，把 key 填进去" -ForegroundColor Yellow
} else {
    Write-Host ".env 已经存在，跳过"
}

Write-Host ""
Write-Host "设置完成！之后每次开工：" -ForegroundColor Green
Write-Host "  .venv\Scripts\Activate.ps1"
Write-Host "  streamlit run app.py"
