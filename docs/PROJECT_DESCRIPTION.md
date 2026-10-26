# FocusGuard — AWS exam-monitoring demo

FocusGuard samples a still webcam image every two seconds after the candidate consents. A human proctor reviews alerts; the software does not decide misconduct. Frames are processed asynchronously by YOLOv8n through ONNX Runtime. Only confirmed incident images are retained as evidence.

## AWS services and Free plan allowances

| Service | Purpose | Always Free allowance / cost note |
|---|---|---|
| AWS Lambda | FastAPI app behind Function URL and SQS-triggered inference worker | 1 million requests and 400,000 GB-seconds/month ongoing allowance |
| Amazon SQS FIFO | Ordered frame work queue and dead-letter queue | 1 million requests/month ongoing allowance; FIFO request metering applies |
| Amazon DynamoDB | One incidents table and one shared per-candidate state table | 25 GB storage, 25 provisioned RCUs, and 25 provisioned WCUs/month in the free allowance; these two tables each reserve 5 RCU/5 WCU |
| Amazon S3 | Private incoming frames and confirmed evidence images | No Always Free allowance in the current Free account plan; eligible new-account credits are time-limited. Incoming objects expire after one day |
| Amazon CloudWatch Logs | Lambda logs, retained for 7 days | No ongoing Always Free allowance listed for this architecture; ingestion and storage are billed by usage |
| AWS SAM / CloudFormation | Infrastructure build and deployment | No separate application runtime; normal AWS service resources above are billed under their own offers |

The current AWS Free account plan is time-limited and can provide credits for eligible services. Because the required design depends on S3 and CloudWatch Logs, it cannot be guaranteed to remain $0 or within Always Free allowances indefinitely. Allowances are account-wide, can be shared with other workloads, and excess use is billable. The table summarizes AWS's published offers; see [AWS Free Tier account plans](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier.html), [Lambda pricing](https://aws.amazon.com/lambda/pricing/), [SQS pricing](https://aws.amazon.com/sqs/pricing/), [DynamoDB pricing](https://aws.amazon.com/dynamodb/pricing/), and [S3 pricing](https://aws.amazon.com/s3/pricing/).

## Flow

```text
Candidate page
  -> POST /api/v1/frame (200 KB maximum; server creates frame_id)
  -> S3 incoming/<exam>/<candidate>/<frame_id>.jpg
  -> SQS FIFO message {exam, cand, frame_id, key}
     MessageGroupId = exam#cand; MessageDeduplicationId = frame_id
  -> Python Lambda handler processes the SQS event directly
  -> worker loads YOLOv8n ONNX once per execution environment and runs evaluate()
  -> one DynamoDB read and one write per frame for all event streaks/cooldowns
  -> confirmed event: S3 evidence/<exam>/<candidate>/...jpg + incident metadata table
  -> delete incoming object; one-day S3 lifecycle rule cleans up leftovers
  -> after three failed receives, SQS moves the message to its FIFO DLQ

Proctor page
  -> token-protected GET /api/v1/incidents?exam_id=... polls every 3 seconds
  -> token-protected GET /api/v1/evidence?key=evidence/... for images
  -> GET /api/v1/audit and CSV export both read the same incidents table
  -> DELETE /api/v1/exam/{exam_id} deletes evidence and marks rows PURGED
```

The incident table uses `exam_id` as partition key and `sk = created_at#incident_id` as sort key; a TTL expires metadata after 30 days. It is the sole source for dashboard polling, audit, and export. The state table uses one `state_key = exam#candidate` item containing PHONE_DETECTED, MULTIPLE_PEOPLE, and NO_FACE_DETECTED streak/cooldown state, with a seven-day TTL. SQS FIFO serializes the updates for a candidate.

S3 blocks all public access and encrypts objects at rest with SSE-S3. Evidence reads go through the API, require the proctor token, accept keys only under `evidence/`, reject `..`, and return `Cache-Control: no-store`. Lambda functions use separate least-privilege roles and the AWS SDK role credentials; there are no static AWS credentials.

## Runtime configuration

`PROCTOR_TOKEN` comes from the required NoEcho CloudFormation parameter. The worker defaults are `CONF_THRESHOLD=0.5`, `STREAK_N=2`, and `COOLDOWN_S=30`. Resource names, queue URL, and `MODEL_PATH` are set in the template. The web Lambda has 512 MB and the worker 2,048 MB / 60 seconds. Both log groups retain data for seven days. There is no VPC, NAT gateway, EC2/ECS/App Runner, provisioned concurrency, or reserved concurrency.
