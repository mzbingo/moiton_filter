param([double]$Threshold = 0.72, [switch]$Refresh)
$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$arguments = @('src/filter_videos.py', '--threshold', "$Threshold")
if ($Refresh) { $arguments += '--refresh' }
& "$PSScriptRoot/.venv/Scripts/python.exe" @arguments
exit $LASTEXITCODE
