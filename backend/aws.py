"""AWS storage, queue and DynamoDB wrappers. Credentials come only from the Lambda role."""
import os
import time
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key

REGION = os.getenv("AWS_REGION", "ap-south-1")
BUCKET = os.environ["DATA_BUCKET"]
QUEUE_URL = os.getenv("FRAME_QUEUE_URL", "")
INCIDENT_TABLE = os.environ["INCIDENT_TABLE"]
STATE_TABLE = os.environ.get("STATE_TABLE", "")
_s3 = _sqs = _incidents = _states = None


class NotFound(Exception):
    pass


def _clients():
    global _s3, _sqs, _incidents, _states
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
        _sqs = boto3.client("sqs", region_name=REGION)
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        _incidents = dynamodb.Table(INCIDENT_TABLE)
        if STATE_TABLE:
            _states = dynamodb.Table(STATE_TABLE)


def publish_frame(data: bytes, exam: str, cand: str, frame_id: str, key: str):
    _clients()
    if not QUEUE_URL:
        raise RuntimeError("FRAME_QUEUE_URL is not configured")
    _s3.put_object(Bucket=BUCKET, Key=key, Body=data, ContentType="image/jpeg")
    try:
        _sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=__import__("json").dumps({"exam": exam, "cand": cand, "frame_id": frame_id, "key": key}),
            MessageGroupId=f"{exam}#{cand}",
            MessageDeduplicationId=frame_id,
        )
    except Exception:
        _s3.delete_object(Bucket=BUCKET, Key=key)
        raise


def read_incoming(key: str) -> bytes:
    _clients()
    return _s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()


def delete_incoming(key: str):
    _clients()
    _s3.delete_object(Bucket=BUCKET, Key=key)


def copy_evidence(source_key: str, evidence_key: str):
    _clients()
    _s3.copy_object(
        Bucket=BUCKET,
        Key=evidence_key,
        CopySource={"Bucket": BUCKET, "Key": source_key},
        ContentType="image/jpeg",
        MetadataDirective="REPLACE",
        ServerSideEncryption="AES256",
    )


def read_evidence(key: str) -> bytes:
    _clients()
    try:
        return _s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    except _s3.exceptions.NoSuchKey as exc:
        raise NotFound from exc
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            raise NotFound from exc
        raise


def get_state(exam: str, cand: str) -> dict:
    _clients()
    response = _states.get_item(Key={"state_key": f"{exam}#{cand}"}, ConsistentRead=True)
    return response.get("Item", {}).get("events", {})


def put_state(exam: str, cand: str, events: dict):
    _clients()
    serialized = {
        name: {
            "streak": int(value.get("streak", 0)),
            "cooldown_until": Decimal(str(value.get("cooldown_until", 0))),
        }
        for name, value in events.items()
    }
    _states.put_item(Item={
        "state_key": f"{exam}#{cand}",
        "events": serialized,
        "ttl": int(time.time()) + 7 * 86400,
    })


def _plain(item):
    if isinstance(item, list):
        return [_plain(v) for v in item]
    if isinstance(item, dict):
        return {k: _plain(v) for k, v in item.items() if k not in ("ttl", "sk")}
    if isinstance(item, Decimal):
        return float(item)
    return item


def add_incident(row: dict):
    _clients()
    item = {k: v for k, v in row.items() if v is not None}
    item["sk"] = f"{row['created_at']}#{row['incident_id']}"
    item["ttl"] = int(time.time()) + 30 * 86400
    item["confidence"] = Decimal(str(row["confidence"]))
    _incidents.put_item(Item=item)


def query_incidents(exam: str, since: str):
    _clients()
    key_condition = Key("exam_id").eq(exam)
    if since:
        # Sort keys start with ISO-8601 UTC timestamps, so lexical ordering matches time.
        key_condition = key_condition & Key("sk").gte(since)
    items = []
    kwargs = {"KeyConditionExpression": key_condition, "ScanIndexForward": False}
    while True:
        response = _incidents.query(**kwargs)
        items.extend(_plain(i) for i in response.get("Items", []))
        if "LastEvaluatedKey" not in response:
            break
        kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
    for item in items:
        item.pop("sk", None)
    return items


def purge_exam(exam: str) -> int:
    _clients()
    deleted = 0
    continuation = None
    prefix = f"evidence/{exam}/"
    while True:
        kwargs = {"Bucket": BUCKET, "Prefix": prefix}
        if continuation:
            kwargs["ContinuationToken"] = continuation
        result = _s3.list_objects_v2(**kwargs)
        keys = [{"Key": obj["Key"]} for obj in result.get("Contents", [])]
        for start in range(0, len(keys), 1000):
            page = keys[start:start + 1000]
            response = _s3.delete_objects(Bucket=BUCKET, Delete={"Objects": page})
            if response.get("Errors"):
                raise RuntimeError(f"failed to purge {len(response['Errors'])} evidence objects")
            deleted += len(response.get("Deleted", []))
        continuation = result.get("NextContinuationToken")
        if not continuation:
            break
    kwargs = {"KeyConditionExpression": Key("exam_id").eq(exam)}
    while True:
        result = _incidents.query(**kwargs)
        for item in result.get("Items", []):
            _incidents.update_item(
                Key={"exam_id": exam, "sk": item["sk"]},
                UpdateExpression="SET #status = :status REMOVE evidence_key",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={":status": "PURGED"},
            )
        if "LastEvaluatedKey" not in result:
            break
        kwargs["ExclusiveStartKey"] = result["LastEvaluatedKey"]
    return deleted
