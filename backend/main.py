import asyncio
import os
import uuid

from fastapi import FastAPI, File, Form, Header, HTTPException, Query, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mangum import Mangum

import aws

PROCTOR_TOKEN = os.getenv("PROCTOR_TOKEN", "")


def auth(tok):
    if not PROCTOR_TOKEN or tok != PROCTOR_TOKEN:
        raise HTTPException(401, "bad token")


def safe(s: str) -> str:
    return "".join(c for c in s if c.isalnum() or c in "-_")[:64] or "x"


app = FastAPI(title="FocusGuard")


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/candidate")
async def candidate_page():
    return FileResponse("static/candidate/index.html", media_type="text/html")


@app.get("/proctor")
async def proctor_page():
    return FileResponse("static/proctor/index.html", media_type="text/html")


@app.post("/events")
async def legacy_worker_event():
    raise HTTPException(404)


@app.post("/api/v1/frame", status_code=202)
async def frame(exam_id: str = Form(...), candidate_id: str = Form(...), file: UploadFile = File(...)):
    raw = await file.read(200_001)
    if len(raw) > 200_000:
        raise HTTPException(413, "frame too large")
    exam, cand, fid = safe(exam_id), safe(candidate_id), str(uuid.uuid4())
    key = f"incoming/{exam}/{cand}/{fid}.jpg"
    await asyncio.to_thread(aws.publish_frame, raw, exam, cand, fid, key)
    return {"frame_id": fid}


@app.get("/api/v1/incidents")
async def incidents(exam_id: str = Query(...), since: str = Query(""), x_proctor_token: str = Header("")):
    auth(x_proctor_token)
    return {"items": await asyncio.to_thread(aws.query_incidents, safe(exam_id), since)}


@app.get("/api/v1/evidence")
async def evidence(key: str = Query(...), x_proctor_token: str = Header("")):
    auth(x_proctor_token)
    if not key.startswith("evidence/") or ".." in key:
        raise HTTPException(400, "bad key")
    try:
        data = await asyncio.to_thread(aws.read_evidence, key)
    except aws.NotFound:
        raise HTTPException(404, "evidence not found")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/v1/audit")
async def audit(exam_id: str = Query(...), x_proctor_token: str = Header("")):
    auth(x_proctor_token)
    items = await asyncio.to_thread(aws.query_incidents, safe(exam_id), "")
    return {"source": "dynamodb", "enabled": True, "items": items}


@app.delete("/api/v1/exam/{exam_id}")
async def purge(exam_id: str, x_proctor_token: str = Header("")):
    auth(x_proctor_token)
    return {"purged": await asyncio.to_thread(aws.purge_exam, safe(exam_id))}


app.mount("/", StaticFiles(directory="static", html=True), name="static")
handler = Mangum(app)
