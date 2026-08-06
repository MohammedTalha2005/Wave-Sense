#!/usr/bin/env python3
"""
WaveSense — Deep Learning (1D-CNN + Bi-LSTM) Console Launcher.

Runs the CNN+LSTM FastAPI backend and serves the dashboard on port 8085.
"""

import os
import sys
import uvicorn

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, "server"))

def main():
    port = int(os.environ.get("PORT", 8080))
    print("============================================================")
    print("  WaveSense — CNN+LSTM Deep Learning Sensing Server")
    print("============================================================")
    print(f"  Dashboard : http://localhost:{port}")
    print(f"  API Docs  : http://localhost:{port}/docs")
    print(f"  CSI POST  : http://localhost:{port}/api/csi")
    print(f"  WS Feed   : ws://localhost:{port}/ws")
    print("============================================================")
    print("  Press Ctrl-C to stop.\n")

    uvicorn.run("presence_api_cnn:app", host="0.0.0.0", port=port, reload=False)

if __name__ == "__main__":
    main()
