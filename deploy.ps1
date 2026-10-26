param(
    [string]$ProctorToken,
    [string]$StackName = "focusguard",
    [string]$Region = "ap-south-1",
    [switch]$BuildOnly
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$repo = $PSScriptRoot
$buildRoot = Join-Path $repo "build"
$webDir = Join-Path $buildRoot "web"
$workerDir = Join-Path $buildRoot "worker"
$workerLayerDir = Join-Path $buildRoot "worker-layer"
$workerLayerPython = Join-Path $workerLayerDir "python"
$artifactDir = Join-Path $repo "artifacts"
$modelPath = Join-Path $repo "models\yolov8n.onnx"
$modelUrl = "https://huggingface.co/Kalray/yolov8/resolve/9e0af089be9c2f172e4fd9b724805f8b6514854e/yolov8n.onnx"

function Reset-Directory([string]$Path) {
    $rootPath = [System.IO.Path]::GetFullPath($buildRoot).TrimEnd('\') + '\'
    $absolutePath = [System.IO.Path]::GetFullPath($Path)
    if (-not $absolutePath.StartsWith($rootPath, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Refusing to reset a path outside build/: $absolutePath" }
    if (Test-Path -LiteralPath $absolutePath) { Remove-Item -LiteralPath $absolutePath -Recurse -Force }
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
}

function Get-DirectoryBytes([string]$Path) {
    $files = Get-ChildItem -LiteralPath $Path -File -Recurse
    if ($files.Count -eq 0) { return [int64]0 }
    return [int64](($files | Measure-Object -Property Length -Sum).Sum)
}

function New-Zip([string]$Source, [string]$Destination) {
    if (Test-Path -LiteralPath $Destination) { Remove-Item -LiteralPath $Destination -Force }
    [System.IO.Compression.ZipFile]::CreateFromDirectory($Source, $Destination, [System.IO.Compression.CompressionLevel]::Optimal, $false)
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw "Python is required on PATH." }
if (-not $BuildOnly -and -not (Get-Command sam -ErrorAction SilentlyContinue)) { throw "AWS SAM CLI is required on PATH." }
if (-not $BuildOnly -and -not (Get-Command aws -ErrorAction SilentlyContinue)) { throw "AWS CLI is required on PATH." }
if (-not $BuildOnly -and [string]::IsNullOrWhiteSpace($ProctorToken)) { throw "Supply -ProctorToken to deploy the stack." }

Reset-Directory $webDir
Reset-Directory $workerDir
Reset-Directory $workerLayerDir
New-Item -ItemType Directory -Path $workerLayerPython -Force | Out-Null
New-Item -ItemType Directory -Path $artifactDir -Force | Out-Null

python -m pip install --disable-pip-version-check --platform manylinux2014_x86_64 --python-version 3.12 --only-binary=:all: --target $webDir -r (Join-Path $repo "backend\requirements-web.txt")
if ($LASTEXITCODE -ne 0) { throw "Installing web dependencies failed." }
Copy-Item (Join-Path $repo "backend\main.py"), (Join-Path $repo "backend\aws.py"), (Join-Path $repo "backend\rules.py") -Destination $webDir
New-Item -ItemType Directory -Path (Join-Path $webDir "static") -Force | Out-Null
Copy-Item (Join-Path $repo "frontend\*") -Destination (Join-Path $webDir "static") -Recurse

# ONNX Runtime publishes cp312 wheels for manylinux_2_27/2_28; OpenCV and
# NumPy publish manylinux2014 wheels. Include all three compatible tags.
python -m pip install --disable-pip-version-check --platform manylinux2014_x86_64 --platform manylinux_2_27_x86_64 --platform manylinux_2_28_x86_64 --python-version 3.12 --only-binary=:all: --target $workerLayerPython -r (Join-Path $repo "backend\requirements-worker.txt")
if ($LASTEXITCODE -ne 0) { throw "Installing worker dependencies failed." }

# Inference does not use ONNX Runtime's symbolic/quantization tools, OpenCV's
# sample cascades, NumPy's test/compile tools, or Windows-only readline shim.
$trimRelativePaths = @(
    "sympy", "mpmath", "pyreadline3", "onnxruntime\tools",
    "onnxruntime\quantization", "onnxruntime\transformers", "cv2\data",
    "numpy\f2py", "numpy\testing", "numpy\core\tests"
)
foreach ($relativePath in $trimRelativePaths) {
    $trimPath = [System.IO.Path]::GetFullPath((Join-Path $workerLayerPython $relativePath))
    $layerRoot = [System.IO.Path]::GetFullPath($workerLayerPython).TrimEnd('\') + '\'
    if (-not $trimPath.StartsWith($layerRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Refusing to trim a path outside the worker layer: $trimPath" }
    if (Test-Path -LiteralPath $trimPath) { Remove-Item -LiteralPath $trimPath -Recurse -Force }
}
Get-ChildItem -LiteralPath $workerLayerPython -Directory -Filter "*.dist-info" | ForEach-Object {
    $metadataPath = [System.IO.Path]::GetFullPath($_.FullName)
    $layerRoot = [System.IO.Path]::GetFullPath($workerLayerPython).TrimEnd('\') + '\'
    if (-not $metadataPath.StartsWith($layerRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Refusing to trim a path outside the worker layer: $metadataPath" }
    Remove-Item -LiteralPath $metadataPath -Recurse -Force
}
Get-ChildItem -LiteralPath $workerLayerPython -Directory -Recurse -Filter "__pycache__" | ForEach-Object {
    $cachePath = [System.IO.Path]::GetFullPath($_.FullName)
    $layerRoot = [System.IO.Path]::GetFullPath($workerLayerPython).TrimEnd('\') + '\'
    if (-not $cachePath.StartsWith($layerRoot, [System.StringComparison]::OrdinalIgnoreCase)) { throw "Refusing to trim a path outside the worker layer: $cachePath" }
    Remove-Item -LiteralPath $cachePath -Recurse -Force
}

Copy-Item (Join-Path $repo "backend\worker.py"), (Join-Path $repo "backend\processor.py"), (Join-Path $repo "backend\aws.py"), (Join-Path $repo "backend\rules.py"), (Join-Path $repo "backend\detector.py") -Destination $workerDir
if (-not (Test-Path -LiteralPath $modelPath)) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $modelPath) -Force | Out-Null
    Invoke-WebRequest -Uri $modelUrl -OutFile $modelPath
}
if ((Get-Item -LiteralPath $modelPath).Length -lt 1000000) { throw "The ONNX model is unexpectedly small." }
New-Item -ItemType Directory -Path (Join-Path $workerDir "models") -Force | Out-Null
Copy-Item -LiteralPath $modelPath -Destination (Join-Path $workerDir "models\yolov8n.onnx")

$limit = [int64](250 * 1MB)
$webBytes = Get-DirectoryBytes $webDir
$workerBytes = Get-DirectoryBytes $workerDir
$workerLayerBytes = Get-DirectoryBytes $workerLayerDir
$workerTotalBytes = $workerBytes + $workerLayerBytes
foreach ($entry in @(@{ Name = "web"; Bytes = $webBytes }, @{ Name = "worker code"; Bytes = $workerBytes }, @{ Name = "worker dependencies layer"; Bytes = $workerLayerBytes }, @{ Name = "worker combined"; Bytes = $workerTotalBytes })) {
    $mb = [Math]::Round($entry.Bytes / 1MB, 2)
    Write-Host ("{0}: {1} MB unzipped" -f $entry.Name, $mb)
    if ($entry.Bytes -ge $limit) { throw "$($entry.Name) package is not under Lambda's 250 MB unzipped limit." }
}

New-Zip $webDir (Join-Path $artifactDir "focusguard-web.zip")
New-Zip $workerDir (Join-Path $artifactDir "focusguard-worker.zip")
New-Zip $workerLayerDir (Join-Path $artifactDir "focusguard-worker-layer.zip")
Write-Host ("web zip: {0:N2} MB" -f ((Get-Item (Join-Path $artifactDir "focusguard-web.zip")).Length / 1MB))
Write-Host ("worker zip: {0:N2} MB" -f ((Get-Item (Join-Path $artifactDir "focusguard-worker.zip")).Length / 1MB))
Write-Host ("worker layer zip: {0:N2} MB" -f ((Get-Item (Join-Path $artifactDir "focusguard-worker-layer.zip")).Length / 1MB))

if ($BuildOnly) { return }

sam deploy `
    --stack-name $StackName `
    --template-file (Join-Path $repo "template.yaml") `
    --resolve-s3 `
    --capabilities CAPABILITY_IAM `
    --region $Region `
    --parameter-overrides "ProctorToken=$ProctorToken" `
    --no-confirm-changeset
if ($LASTEXITCODE -ne 0) { throw "SAM deployment failed." }

$outputs = aws cloudformation describe-stacks --stack-name $StackName --region $Region --query "Stacks[0].Outputs" --output json | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw "Could not read CloudFormation outputs." }
$candidate = ($outputs | Where-Object OutputKey -eq "CandidateUrl").OutputValue
$proctor = ($outputs | Where-Object OutputKey -eq "ProctorUrl").OutputValue
Write-Host "Candidate URL: $candidate"
Write-Host "Proctor URL:   $proctor"
