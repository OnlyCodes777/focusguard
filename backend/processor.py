import asyncio
import datetime as dt
import logging
import os
import uuid

import cv2
import numpy as np

import aws
from detector import Detector
from rules import advance_state, evaluate

log = logging.getLogger("focusguard")
CONF = float(os.getenv("CONF_THRESHOLD", "0.5"))
STREAK_N = int(os.getenv("STREAK_N", "2"))
COOLDOWN_S = int(os.getenv("COOLDOWN_S", "30"))
det = None


async def process_frame(exam, cand, fid, key):
    global det
    raw = await asyncio.to_thread(aws.read_incoming, key)
    img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if img is not None and det is None:
        det = Detector(os.getenv("MODEL_PATH", "models/yolov8n.onnx"), CONF)
    detections = await asyncio.to_thread(det.detect, img) if img is not None else []
    events = evaluate(detections)
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    # FIFO MessageGroupId serializes one exam/candidate. This is one state read
    # and one state write per frame, covering all event types together.
    state = await asyncio.to_thread(aws.get_state, exam, cand)
    incidents, state = advance_state(events, state, now, STREAK_N, COOLDOWN_S)
    for event_type in ("PHONE_DETECTED", "MULTIPLE_PEOPLE", "NO_FACE_DETECTED"):
        if event_type in incidents:
            incident_id = str(uuid.uuid4())
            evidence_key = f"evidence/{exam}/{cand}/{fid}-{event_type}.jpg"
            await asyncio.to_thread(aws.copy_evidence, key, evidence_key)
            created_at = dt.datetime.now(dt.timezone.utc).isoformat()
            row = {
                "incident_id": incident_id,
                "exam_id": exam,
                "candidate_id": cand,
                "frame_id": fid,
                "event_type": event_type,
                "confidence": round(incidents[event_type], 3),
                "created_at": created_at,
                "evidence_key": evidence_key,
                "status": "NEW",
            }
            await asyncio.to_thread(aws.add_incident, row)
    await asyncio.to_thread(aws.put_state, exam, cand, state)
    try:
        await asyncio.to_thread(aws.delete_incoming, key)
    except Exception:
        # Processing is complete; S3 lifecycle cleanup is the fallback.
        log.exception("could not delete processed incoming frame %s", key)
