# Держит бота запущенным: если процесс упал или его убили — поднимает снова.
# Запускается задачей Планировщика Windows "ChessBot" (см. README, раздел «Windows»).
$ErrorActionPreference = "Continue"
Set-Location -LiteralPath $PSScriptRoot

$python = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\pythonw.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $python = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
}
if (-not $python) {
    Add-Content -LiteralPath "data\bot.log" -Value "$(Get-Date -Format s) ERROR pythonw.exe not found"
    exit 1
}

while ($true) {
    Start-Process -FilePath $python -ArgumentList "main.py" -WorkingDirectory $PSScriptRoot -Wait -NoNewWindow
    Start-Sleep -Seconds 10
}
