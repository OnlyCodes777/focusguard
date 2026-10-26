import asyncio
import json
import logging

from processor import process_frame

log = logging.getLogger("focusguard")


def handler(event, context):
    failures = []
    for record in event.get("Records", []):
        try:
            message = json.loads(record["body"])
            asyncio.run(process_frame(message["exam"], message["cand"], message["frame_id"], message["key"]))
        except Exception:
            log.exception("frame failed: %s", record.get("messageId"))
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
