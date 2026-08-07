#!/bin/bash
# ============================================================
#  WifiIdentification — ESP32 Setup & Flash Script
#  Run this once to install ESP-IDF and flash both ESP32s.
#  Usage:  bash setup_esp.sh
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$HOME/Wifi_Detection/ESP32_Firmware/esp-idf/export.sh" ]; then
    IDF_DIR="$HOME/Wifi_Detection/ESP32_Firmware/esp-idf"
else
    IDF_DIR="$SCRIPT_DIR/ESP32_Firmware/esp-idf"
fi
IDF_VERSION="v5.4"

TX_PORT="/dev/ttyACM1"
RX_PORT="/dev/ttyACM0"

echo ""
echo "============================================================"
echo "  WifiIdentification — ESP32 Setup"
echo "============================================================"
echo ""

# ── Step 1: Install ESP-IDF if missing ──────────────────────
if [ ! -f "$IDF_DIR/export.sh" ]; then
    echo "[1/4] ESP-IDF not found. Downloading $IDF_VERSION ..."
    mkdir -p "$HOME/Wifi_Detection/ESP32_Firmware"
    git clone \
        --recursive \
        --depth 1 \
        --branch "$IDF_VERSION" \
        https://github.com/espressif/esp-idf.git \
        "$IDF_DIR"
    echo "[1/4] Download complete."
else
    echo "[1/4] ESP-IDF already installed at $IDF_DIR ✅"
fi

# ── Step 2: Install ESP-IDF tools ───────────────────────────
echo ""
echo "[2/4] Installing ESP-IDF tools for ESP32-S3 ..."
"$IDF_DIR/install.sh" esp32s3
echo "[2/4] Tools installed for ESP32-S3 ✅"

# ── Step 3: Source environment ───────────────────────────────
echo ""
echo "[3/4] Activating ESP-IDF environment ..."
source "$IDF_DIR/export.sh"

# ── Step 4: Flash devices ────────────────────────────────────
echo ""
echo "[4/4] Ready to flash. Choose an option:"
echo ""
echo "  1) Flash Transmitter only  ($TX_PORT)"
echo "  2) Flash Receiver only     ($RX_PORT)"
echo "  3) Flash BOTH"
echo "  4) Exit (just install IDF, flash later)"
echo ""
read -rp "Enter choice [1-4]: " choice

case "$choice" in
  1)
    echo "Flashing Transmitter on $TX_PORT ..."
    cd "$SCRIPT_DIR/esp32/transmitter"
    rm -rf build sdkconfig
    idf.py set-target esp32s3
    idf.py -p "$TX_PORT" build flash monitor
    ;;
  2)
    echo "Flashing Receiver on $RX_PORT ..."
    cd "$SCRIPT_DIR/esp32/receiver"
    rm -rf build sdkconfig
    idf.py set-target esp32s3
    idf.py -p "$RX_PORT" build flash monitor
    ;;
  3)
    echo "Flashing Transmitter on $TX_PORT ..."
    cd "$SCRIPT_DIR/esp32/transmitter"
    rm -rf build sdkconfig
    idf.py set-target esp32s3
    idf.py -p "$TX_PORT" build flash
    echo ""
    echo "Flashing Receiver on $RX_PORT ..."
    cd "$SCRIPT_DIR/esp32/receiver"
    rm -rf build sdkconfig
    idf.py set-target esp32s3
    idf.py -p "$RX_PORT" build flash
    echo ""
    echo "Both devices flashed ✅"
    echo ""
    echo "To monitor:"
    echo "  Terminal 1: source $IDF_DIR/export.sh && idf.py -p $TX_PORT monitor"
    echo "  Terminal 2: source $IDF_DIR/export.sh && idf.py -p $RX_PORT monitor"
    ;;
  4)
    echo ""
    echo "ESP-IDF installed. To flash later, run:"
    echo "  source $IDF_DIR/export.sh"
    echo "  cd $SCRIPT_DIR/esp32/transmitter && idf.py -p $TX_PORT build flash monitor"
    echo "  cd $SCRIPT_DIR/esp32/receiver    && idf.py -p $RX_PORT build flash monitor"
    ;;
  *)
    echo "Invalid choice. Exiting."
    exit 1
    ;;
esac

echo ""
echo "============================================================"
echo "  Done! To start the server:"
echo "    cd $SCRIPT_DIR"
echo "    pip install -r requirements.txt"
echo "    python3 run.py"
echo "============================================================"
