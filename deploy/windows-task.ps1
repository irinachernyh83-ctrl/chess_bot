# Регистрирует задачу Планировщика Windows "ChessBot":
# запуск при входе в систему + перезапуск бота через run_forever.ps1.
# Запуск:  powershell -ExecutionPolicy Bypass -File deploy\windows-task.ps1
$ErrorActionPreference = "Stop"

$project = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runner = Join-Path $project "run_forever.ps1"
if (-not (Test-Path -LiteralPath $runner)) {
    throw "Не найден run_forever.ps1 в $project"
}

if (Get-ScheduledTask -TaskName "ChessBot" -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName "ChessBot" -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like "*main.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`"" `
    -WorkingDirectory $project

$trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask `
    -TaskName "ChessBot" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Description "Шахматный Telegram-бот (@skldlgkjsjskfkkfheja_bot)" `
    -Force | Out-Null

Start-ScheduledTask -TaskName "ChessBot"
Start-Sleep -Seconds 8

Get-ScheduledTask -TaskName "ChessBot" | Select-Object TaskName, State
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Select-Object ProcessId, CreationDate
