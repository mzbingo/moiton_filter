$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $python -m pip install 'pyinstaller==6.22.3'
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed' }
# Qt needs the newer runtime shipped with PySide6, not Python's older bundled CRT.
$qtRuntime = Join-Path $PSScriptRoot '.venv\Lib\site-packages\PySide6'
# Avoid picking another tool's ICU DLL from PATH instead of Windows' ICU runtime.
$buildOriginalPath = $env:PATH
try {
    $env:PATH = "$PSScriptRoot\.venv\Scripts;$env:SystemRoot\System32;$env:SystemRoot"
    & $python -m PyInstaller --noconfirm --clean --onefile --windowed --name '人物视频筛选' --paths src --distpath . --workpath build/gui --specpath build --exclude-module torch --exclude-module cv2 --exclude-module numpy --add-binary "$qtRuntime\vcruntime140.dll;." --add-binary "$qtRuntime\vcruntime140_1.dll;." src/desktop_app.py
} finally {
    $env:PATH = $buildOriginalPath
}
if ($LASTEXITCODE -ne 0) { throw 'GUI build failed' }
