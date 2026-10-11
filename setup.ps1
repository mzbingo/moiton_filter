$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.venv/Scripts/python.exe')) {
    python -m venv .venv
    if ($LASTEXITCODE) { throw 'Could not create Python environment' }
}
& ./.venv/Scripts/python.exe -m pip install torch==2.11.0 torchvision==0.26.0 --index-url https://download.pytorch.org/whl/cu128
if ($LASTEXITCODE) { throw 'PyTorch installation failed' }
& ./.venv/Scripts/python.exe -m pip install -r requirements.txt
if ($LASTEXITCODE) { throw 'Dependency installation failed' }
New-Item -ItemType Directory -Force models | Out-Null
if (-not (Test-Path 'models/osnet_msmt17.pth')) {
    Invoke-WebRequest 'https://huggingface.co/kaiyangzhou/osnet/resolve/main/osnet_x1_0_msmt17_combineall_256x128_amsgrad_ep150_stp60_lr0.0015_b64_fb10_softmax_labelsmooth_flip_jitter.pth' -OutFile 'models/osnet_msmt17.pth.partial'
    Move-Item -LiteralPath 'models/osnet_msmt17.pth.partial' -Destination 'models/osnet_msmt17.pth'
}
& ./.venv/Scripts/python.exe -c "from ultralytics import YOLO; YOLO('models/yolo11s.pt'); YOLO('models/yolo11s-pose.pt')"
if ($LASTEXITCODE) { throw 'Detector download failed' }
