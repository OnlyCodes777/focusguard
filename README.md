# FocusGuard on AWS

FocusGuard is a consent-based exam-proctoring demo. The candidate page samples a webcam still every two seconds; a human proctor reviews alerts. It does not record continuous video or decide misconduct.

## Architecture

```text
Candidate browser
  -> public Lambda Function URL /api/v1/frame
  -> S3 incoming/<exam>/<candidate>/<frame>.jpg
  -> SQS FIFO {exam, cand, frame_id, key}
  -> Python 3.12 x86_64 worker Lambda (ZIP, 2,048 MB, 60 seconds)
  -> ONNX Runtime, OpenCV, NumPy in a Lambda layer
  -> YOLOv8n + shared DynamoDB state (streak/cooldown)
  -> confirmed incident: private S3 evidence/ + DynamoDB incident metadata
  -> incoming object deleted (one-day S3 lifecycle is the fallback)

Proctor browser
  -> same Function URL, token-protected API
  -> poll incident table every 3 seconds; fetch evidence through API
  -> export CSV from that same incident table
```

The web function is a Python 3.12 x86_64 ZIP running FastAPI through Mangum. The worker is a separate Python 3.12 x86_64 ZIP with a direct SQS Lambda handler and a dependency layer containing ONNX Runtime, OpenCV, and NumPy. The build trims optional inference tooling, test files, and package metadata; the remaining worker code plus layer must fit Lambda's [combined 250 MB unzipped quota](https://docs.aws.amazon.com/lambda/latest/dg/python-package.html). Both functions use Lambda's provided boto3; boto3 is not bundled. The FIFO message contains only the S3 key and identifiers, never image bytes. A FIFO group per exam/candidate preserves state-update order. Failed frame messages move to a dead-letter queue after three receives.

## Deploy from Windows PowerShell

Prerequisites: Python with pip, AWS CLI authenticated through your normal profile/SSO/role, and AWS SAM CLI. The worker wheel install selects Python 3.12-compatible Linux x86_64 wheels, so the local Python does not need to be 3.12. Docker and WSL are not used. The AWS identity needs permission to create Lambda, S3, SQS, DynamoDB, IAM, CloudFormation, CloudWatch Logs, and SAM's deployment artifact bucket. Deploy to `ap-south-1`.

From the repository folder, run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
./deploy.ps1 -ProctorToken '<your-proctor-token>'
```

To only build the packages and report their sizes without requiring SAM, AWS CLI, or a proctor token, run:

```powershell
./deploy.ps1 -BuildOnly
```

The script installs Linux x86_64 Python 3.12 binary wheels into `build/web` and `build/worker-layer/python` using pip's cross-platform wheel options. The worker model is `models/yolov8n.onnx` (the script downloads it if missing). The script creates `artifacts/focusguard-web.zip`, `artifacts/focusguard-worker.zip`, and `artifacts/focusguard-worker-layer.zip`, reports the uncompressed web, worker code, layer, and combined worker sizes plus each ZIP size, then runs `sam deploy --resolve-s3 --region ap-south-1`. SAM uploads the local `build/web`, `build/worker`, and `build/worker-layer` directories as Lambda ZIP packages. The script stops if either function plus its layers reaches 250 MB unzipped and prints the Candidate and Proctor URLs after deployment. Do not commit the token or paste it into logs.

## Local rules smoke test

No AWS account, credentials, detector model, or running service is required for this test:

```powershell
python -m unittest discover -s tests -v
```

## Teardown

This empties the deployed evidence bucket and deletes the stack:

```powershell
./teardown.ps1
```

## Configuration

| Variable | Used by | Default / source |
|---|---|---|
| `PROCTOR_TOKEN` | Web API | Required `ProctorToken` CloudFormation parameter; no default |
| `DATA_BUCKET` | Web and worker | Generated private S3 bucket name |
| `FRAME_QUEUE_URL` | Web | Generated SQS FIFO queue URL |
| `INCIDENT_TABLE` | Web and worker | Generated incidents DynamoDB table name |
| `STATE_TABLE` | Worker | Generated DynamoDB state table name |
| `CONF_THRESHOLD` | Worker | `0.5` |
| `STREAK_N` | Worker | `2` |
| `COOLDOWN_S` | Worker | `30` seconds |
| `MODEL_PATH` | Worker | `/var/task/models/yolov8n.onnx` |
| `AWS_REGION` | SDK | Set by Lambda (`ap-south-1`) |

AWS SDK credentials are supplied by each Lambda's execution role. No AWS access key or secret is configured in the template or scripts.

## Free-plan cost boundary

Lambda, SQS, and provisioned DynamoDB have ongoing Always Free allowances. This design also requires S3 and CloudWatch Logs; AWS does not list those as Always Free in the current Free account plan. S3 is eligible for new-account credits, which are time-limited, and CloudWatch Logs is billed by usage. Therefore this architecture cannot be guaranteed to stay at $0 or entirely inside Always Free services indefinitely. At low demo traffic it is designed to use little storage and logging; review billing after deployment and remove the stack when finished.

The web Lambda is public at the Function URL, but proctor and evidence/incident administration routes require the shared token. The candidate frame route accepts JPEG uploads up to 200 KB and assigns frame IDs on the server.

## Changed files in the ZIP packaging update

- Removed: `Dockerfile`, `.dockerignore`, `deploy.sh`, `teardown.sh`, `run_web.sh`.
- Added: `.gitignore`, `backend/processor.py`, `backend/worker.py`, `backend/requirements-web.txt`, `backend/requirements-worker.txt`, `deploy.ps1`, `teardown.ps1`, `models/yolov8n.onnx`.
- Removed: `backend/requirements.txt` (replaced by per-function dependency lists).
- Updated: `backend/main.py`, `template.yaml`, `README.md`, `docs/PROJECT_DESCRIPTION.md`.

Exact deploy and teardown commands are shown above. The ZIP sizes are printed by `deploy.ps1` every time it builds the deployment packages.
