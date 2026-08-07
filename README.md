# WaveSense — Wi-Fi CSI Real-Time Presence Detection & 24-Hour Analytics Platform

WaveSense is an ambient intelligence platform that uses **Wi-Fi Channel State Information (CSI)** for real-time human presence detection, physical disturbance logging, restricted-hours security alerts, and automated continuous 24-hour occupancy reporting.

Unlike traditional motion sensors or cameras, WaveSense requires no line-of-sight, protects personal privacy, and leverages commodity Wi-Fi signals passing through physical spaces using a hybrid **1D-CNN + BiLSTM Deep Learning model**.

---

## 🌟 Key Features

* **Deep Learning Sensing Engine:** High-precision 1D-CNN + BiLSTM architecture with temporal hysteresis smoothing.
* **Restricted Hours Security Alert Engine:** Set break times (12h/24h) to trigger live visual alerts and audit logging on unauthorized room presence.
* **Continuous 24/7 Excel/CSV Logging:** Automatic background telemetry collection and hourly 24-hour occupancy summaries (`Daily_Summary_YYYY-MM-DD.csv`).
* **Zero-Configuration Auto-Discovery:** ESP32 firmware dynamically discovers the server IP via a UDP beacon (Port 8089) and saves it to Non-Volatile Flash Storage (NVS).
* **Modern Web Dashboard:** Live WebSocket dashboard with subcarrier spectrum graphs, RSSI gauges, and real-time security consoles.

---

## 📁 Workspace Directory Structure

```
WifiIdentification/
├── cnn_lstm/                 # 1D-CNN + BiLSTM Sensing Engine & Web Server
│   ├── dashboard/            # Web User Interface (presence.html)
│   ├── server/               # Deep Learning Engine, FastAPI Gateway & Daily Logger
│   └── run_cnn_lstm.py       # Primary System Launcher
├── esp32/                    # ESP32 C Firmware Source Subsystem
│   └── receiver/             # ESP32 Receiver Firmware (main.c, CMakeLists.txt)
├── ESP32_Firmware/           # ESP-IDF SDK (Toolchain & Build Environment)
├── reports/                  # 24/7 Daily Telemetry Logs & Excel Summaries
├── data/                     # Labeled Training Datasets
├── setup_esp.sh              # ESP-IDF Auto-Setup & Flashing Script
├── README.md                 # System Startup Guide
└── requirements.txt          # Python Package Dependencies
```

---

## ⚡ Quick Start Guide for New Users

### Step 1: Install Python Dependencies (Only Once)
Ensure Python 3.10+ is installed on your host system:
```bash
cd ~/WifiIdentification
pip install -r requirements.txt
```

---
#### Option A: Automatic Interactive Script (Recommended)
```bash
cd ~/WifiIdentification
bash setup_esp.sh
```

#### Option B: Manual Command (using /dev/ttyACM0 or /dev/ttyUSB0)
```bash
source ~/Wifi_Detection/ESP32_Firmware/esp-idf/export.sh
cd ~/WifiIdentification/esp32/receiver
idf.py -p /dev/ttyACM0 build flash monitor
```
> **Automatic IP Discovery:** Once booted, the ESP32 listens for the server's UDP beacon on port `8089`, updates its target URL dynamically, and stores the IP in NVS Flash.

### Step 3: Launch the WaveSense Deep Learning Server
Run the primary backend script:
```bash
python3 cnn_lstm/run_cnn_lstm.py
```
* **Web Dashboard:** Access in browser at [http://localhost:8080](http://localhost:8080)
* **REST API Documentation:** Available at [http://localhost:8080/docs](http://localhost:8080/docs)
* **UDP Beacon Broadcaster:** Starts automatically on UDP port `8089` to broadcast server IP to ESP32 receivers.

---



---

### Step 4: 24/7 Daily Excel Reports & Restricted Hours Alerts
1. Open [http://localhost:8080](http://localhost:8080) in your browser.
2. Navigate to **`Presence Alerts`** in the sidebar to configure break hours (e.g. `12:00` - `12:30`).
3. Navigate to **`Excel Reports`** in the sidebar to generate and download daily occupancy summaries (`Daily_Summary_YYYY-MM-DD.csv`).

---

## 📡 API Reference

| Method | Endpoint | Description |
| :--- | :--- | :--- |
| `POST` | `/api/csi` | Ingests CSI packets from ESP32 receiver |
| `GET` | `/api/reports` | Lists generated 24/7 raw logs and hourly daily summaries |
| `POST` | `/api/reports/generate_summary` | Triggers immediate hourly aggregation daily summary generation |
| `GET` | `/api/reports/download/{filename}` | Securely downloads Excel/CSV report file |
| `POST` | `/api/record/start` | Starts recording labeled training dataset |
| `POST` | `/api/record/stop` | Terminates labeled recording session |
| `POST` | `/api/train` | Triggers background 1D-CNN + BiLSTM model retraining |
| `WS` | `/ws` | Real-time WebSocket feed for dashboard live charts |
