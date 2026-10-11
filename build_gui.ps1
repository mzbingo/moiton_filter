$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $python -m pip install 'pyinstaller==6.22.3'
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller installation failed' }
& $python -m PyInstaller --noconfirm --clean --onefile --windowed --name '人物视频筛选' --paths src --distpath . --workpath build/gui --specpath build --exclude-module torch --exclude-module cv2 --exclude-module numpy src/desktop_app.py
if ($LASTEXITCODE -ne 0) { throw 'GUI build failed' }
