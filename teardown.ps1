param(
    [string]$StackName = "focusguard",
    [string]$Region = "ap-south-1"
)

$ErrorActionPreference = "Stop"
$bucket = aws cloudformation describe-stack-resource `
    --stack-name $StackName `
    --logical-resource-id FocusGuardBucket `
    --region $Region `
    --query "StackResourceDetail.PhysicalResourceId" `
    --output text 2>$null
if ($LASTEXITCODE -eq 0 -and $bucket -and $bucket -ne "None") {
    aws s3 rm "s3://$bucket" --recursive --region $Region
    if ($LASTEXITCODE -ne 0) { throw "Could not empty the FocusGuard S3 bucket." }
}
sam delete --stack-name $StackName --region $Region --no-prompts
if ($LASTEXITCODE -ne 0) { throw "SAM stack deletion failed." }
