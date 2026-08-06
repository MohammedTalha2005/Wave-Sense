# 📁 WaveSense: Directory & Codebase Architecture Report

---

## 📌 Minimal Workspace Directory Tree

```
WifiIdentification/
├── cnn_lstm/                 # 1D-CNN + BiLSTM Sensing Engine & Web Server
│   ├── dashboard/            # Web User Interface (presence.html)
│   ├── server/               # Deep Learning Engine, FastAPI Gateway & Daily Logger
│   └── run_cnn_lstm.py       # Primary System Launcher
├── esp32/                    # ESP32 Receiver C Firmware Subsystem
├── reports/                  # 24/7 Daily Telemetry Logs & Excel Summaries
├── data/                     # Labeled Training Datasets
├── README.md                 # System Startup Guide
└── requirements.txt          # Python Package Dependencies
```

---

## 🔍 Key Module Overview

### 1. `cnn_lstm/` — Deep Learning Sensing Engine & Web Console
Core application package managing real-time neural network inference, web user interface, and continuous logging.

* **`run_cnn_lstm.py`**: Execution entry point that launches the FastAPI backend server on port `8080`.
* **`dashboard/presence.html`**: Single-page web console featuring real-time presence monitoring, security alert management, 24/7 report downloads, and signal telemetry charts.
* **`server/cnn_presence_counter.py`**: PyTorch **1D-CNN + BiLSTM** neural network with a 2-second temporal hysteresis state machine.
* **`server/daily_logger.py`**: 24/7 continuous telemetry logger and automated hourly Excel summary compiler.
* **`server/presence_api_cnn.py`**: FastAPI backend exposing WebSockets streams and REST API endpoints for reports and downloads.

---

### 2. `esp32/` — ESP32 Receiver Firmware
Embedded C firmware for the ESP32 microcontroller board:
* **Promiscuous CSI Sensing**: Captures 64-subcarrier OFDM channel state information from ambient Wi-Fi packets.
* **UDP Auto-Discovery**: Listens on UDP port `8089` to discover the server IP address automatically.
* **Telemetry Streaming**: Transmits subcarrier vectors to `/api/csi` via HTTP POST.

---

### 3. `reports/` — 24/7 Daily CSV Logs & Excel Summaries
Stores raw continuous 2-second telemetry streams (`presence_log_YYYY-MM-DD.csv`) and aggregated 24-hour hourly occupancy summaries (`Daily_Summary_YYYY-MM-DD.csv`) compatible with Microsoft Excel, Apple Numbers, and Google Sheets.
Wait, let's keep it minimal and concise!
