"""
FastAPI Server for CNN+LSTM Deep Learning WaveSense Console.

Serves on port 8085 (or configurable via CLI/environment).
Endpoints:
- POST /api/csi: Receives ESP32 CSI packets
- WS /ws: Real-time telemetry feed
- POST /api/record/start, /api/record/stop: Dataset collection
- POST /api/train: Live training & model hot-swap
"""

import os
import sys
import json
import time
import socket
import asyncio
import threading
from typing import Set
import numpy as np

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE_DIR, "server"))

from cnn_presence_counter import CNNLSTMPresenceCounter
from daily_logger import DailyPresenceLogger

app = FastAPI(title="WaveSense CNN+LSTM Sensing Server")

counter = CNNLSTMPresenceCounter()
daily_logger = DailyPresenceLogger()
active_websockets: Set[WebSocket] = set()

# Live recording session variables
is_recording = False
recording_label = 0
current_record_file = None
record_file_handle = None

# Static & Dashboard Mounts
DASHBOARD_DIR = os.path.join(BASE_DIR, "dashboard")
app.mount("/static", StaticFiles(directory=DASHBOARD_DIR), name="static")

def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('172.18.255.255', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return '127.0.0.1'

def udp_beacon_loop():
    local_ip = get_local_ip()
    port = int(os.environ.get("PORT", 8080))
    print(f"[CNN Discovery] Auto-broadcasting server IP ({local_ip}) on UDP port 8089...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    beacon_msg = f"SERVER_BEACON:{local_ip}:{port}".encode("utf-8")
    while True:
        try:
            sock.sendto(beacon_msg, ('255.255.255.255', 8089))
        except Exception:
            pass
        time.sleep(2.0)

@app.on_event("startup")
async def startup_event():
    beacon_thread = threading.Thread(target=udp_beacon_loop, daemon=True)
    beacon_thread.start()

@app.get("/", response_class=HTMLResponse)
async def get_dashboard():
    index_path = os.path.join(DASHBOARD_DIR, "presence.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return f.read()

@app.post("/api/csi")
async def ingest_csi(request: Request):
    global is_recording, record_file_handle
    try:
        body = await request.json()
        if isinstance(body, list):
            pkts = body
        else:
            pkts = [body]

        for pkt in pkts:
            pred = counter.process_packet(pkt)
            rssi_val = pkt.get("rssi", -65.0)

            if pred:
                daily_logger.log_inference(
                    state=pred["state"],
                    confidence=pred["confidence"],
                    rssi=rssi_val,
                    motion_var=0.0
                )

            # Record if session active
            if is_recording and record_file_handle is not None:
                pkt_to_save = dict(pkt)
                pkt_to_save["label"] = recording_label
                record_file_handle.write(json.dumps(pkt_to_save) + "\n")
                record_file_handle.flush()

            # Broadcast telemetry update to keep UI online & update charts
            if active_websockets:
                telemetry_msg = {
                    "type": "csi_update",
                    "rssi": rssi_val,
                    "csi": pkt.get("csi", pkt.get("csi_data", pkt.get("csi_raw", []))),
                    "timestamp": time.time()
                }
                asyncio.create_task(broadcast_ws(telemetry_msg))

            # Broadcast prediction over WebSockets
            if pred and active_websockets:
                msg = {
                    "type": "prediction",
                    "state": pred["state"],
                    "label": pred["label"],
                    "confidence": pred["confidence"],
                    "p_absent": pred["p_absent"],
                    "p_present": pred["p_present"],
                    "model_type": pred["model_type"],
                    "packets": counter.total_packets,
                    "timestamp": time.time()
                }
                asyncio.create_task(broadcast_ws(msg))

        return {"status": "ok", "received": len(pkts)}
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

@app.get("/api/reports")
async def list_reports():
    return daily_logger.list_reports()

@app.get("/api/reports/download/{filename}")
async def download_report(filename: str):
    fpath = os.path.join(daily_logger.reports_dir, filename)
    if not os.path.exists(fpath):
        raise HTTPException(status_code=404, detail="Report file not found")
    return FileResponse(path=fpath, filename=filename, media_type="text/csv")

@app.post("/api/reports/generate_summary")
async def generate_summary_now():
    summary_path = daily_logger.generate_daily_summary()
    if summary_path:
        fname = os.path.basename(summary_path)
        return {"status": "ok", "summary_file": fname}
    return JSONResponse(status_code=500, content={"error": "Failed to generate daily summary"})

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    active_websockets.add(ws)
    try:
        while True:
            # Keep-alive receive loop
            await ws.receive_text()
    except WebSocketDisconnect:
        active_websockets.remove(ws)
    except Exception:
        active_websockets.discard(ws)

async def broadcast_ws(data: dict):
    msg_text = json.dumps(data)
    dead = set()
    for ws in list(active_websockets):
        try:
            await ws.send_text(msg_text)
        except Exception:
            dead.add(ws)
    for ws in dead:
        active_websockets.discard(ws)

@app.post("/api/record/start")
async def start_recording(request: Request):
    global is_recording, recording_label, current_record_file, record_file_handle
    data = await request.json()
    label = int(data.get("label", 0))

    target_dir = os.path.join(os.path.dirname(BASE_DIR), "data", "presence_labeled")
    os.makedirs(target_dir, exist_ok=True)

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    fname = f"{timestamp}_label{label}_rx01.json"
    fpath = os.path.join(target_dir, fname)

    record_file_handle = open(fpath, "a", encoding="utf-8")
    is_recording = True
    recording_label = label
    current_record_file = fpath

    print(f"[CNN Record] Started recording session — label={label} file={fname}")
    return {"status": "started", "label": label, "file": fname}

@app.post("/api/record/stop")
async def stop_recording():
    global is_recording, record_file_handle, current_record_file
    if is_recording:
        is_recording = False
        if record_file_handle:
            record_file_handle.close()
            record_file_handle = None
        print(f"[CNN Record] Stopped recording session — file={current_record_file}")
        return {"status": "stopped", "file": current_record_file}
    return {"status": "not_recording"}

@app.post("/api/train")
async def trigger_training():
    def run_trainer():
        import subprocess
        print("[CNN Train] Starting PyTorch CNN+LSTM training...", flush=True)
        train_script = os.path.join(BASE_DIR, "server", "train_cnn_lstm.py")
        env = os.environ.copy()
        env["PYTHONUNBUFFERED"] = "1"
        subprocess.run([sys.executable, train_script], env=env)
        counter.reload_model()
        print("[CNN Train] ✅ Model reloaded into live memory.", flush=True)

    threading.Thread(target=run_trainer, daemon=True).start()
    return {"status": "training_started", "message": "CNN+LSTM model training started in background."}
