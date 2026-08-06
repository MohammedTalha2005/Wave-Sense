"""
CNN + LSTM Training Pipeline for Wi-Fi CSI Spatial-Temporal Presence Detection.

Reads labeled data from data/presence_labeled/*.json
Constructs (N, 10, 64) CSI amplitude matrices.
Trains PyTorch CSI_CNN_LSTM model and saves weights to models/presence_cnn_lstm.pt.
"""

import os
import glob
import json
import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import TensorDataset, DataLoader

from cnn_lstm_model import create_model

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(os.path.dirname(BASE_DIR), "data", "presence_labeled")
MODEL_SAVE_PATH = os.path.join(BASE_DIR, "models", "presence_cnn_lstm.pt")

def parse_csi_raw(raw):
    """Parses raw CSI subcarriers into 64 amplitude values."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return None
    if not isinstance(raw, list) or len(raw) == 0:
        return None
    
    amps = []
    for item in raw:
        if isinstance(item, (int, float)):
            amps.append(abs(float(item)))
        elif isinstance(item, list) and len(item) >= 2:
            amps.append(np.hypot(item[0], item[1]))
    
    if len(amps) == 0:
        return None
    
    if len(amps) < 64:
        amps += [0.0] * (64 - len(amps))
    else:
        amps = amps[:64]
        
    return np.array(amps, dtype=np.float32)

def load_dataset(window_size=10):
    files = glob.glob(os.path.join(DATA_DIR, "*.json"))
    if not files:
        print(f"[Train] No labeled files found in {DATA_DIR}")
        return None, None

    X_list = []
    y_list = []

    for fpath in files:
        fname = os.path.basename(fpath)
        # Determine label from filename (label0 = Absent, label1 = Present)
        if "_label1_" in fname or "present" in fname.lower():
            label = 1
        elif "_label0_" in fname or "empty" in fname.lower() or "absent" in fname.lower():
            label = 0
        else:
            continue

        try:
            with open(fpath, 'r') as f:
                pkts = []
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        pkt = json.loads(line)
                        raw = pkt.get("csi", pkt.get("csi_data", pkt.get("csi_raw", [])))
                        amps = parse_csi_raw(raw)
                        if amps is not None:
                            pkts.append(amps)
                    except Exception:
                        continue
                
                if len(pkts) < window_size:
                    continue
                
                # Sliding window of (10, 64)
                for i in range(len(pkts) - window_size + 1):
                    win = np.array(pkts[i:i+window_size], dtype=np.float32)
                    # Normalize per window
                    mean = np.mean(win)
                    std = np.std(win) + 1e-6
                    win_norm = (win - mean) / std
                    
                    X_list.append(win_norm)
                    y_list.append(label)
        except Exception as e:
            print(f"[Train] Error loading {fname}: {e}")

    if not X_list:
        print("[Train] No valid CSI windows extracted.")
        return None, None

    X = np.array(X_list, dtype=np.float32)
    y = np.array(y_list, dtype=np.int64)
    return X, y

def train():
    print("============================================================", flush=True)
    print("  CNN + LSTM PyTorch Training Pipeline Starting", flush=True)
    print("============================================================", flush=True)
    
    X, y = load_dataset(window_size=10)
    if X is None or len(X) == 0:
        print("[Train] Aborting: Dataset is empty.", flush=True)
        return False

    print(f"[Train] Total Extracted CSI Windows: {len(X)} (Shape: {X.shape})", flush=True)
    print(f"[Train] Label distribution: Absent(0)={np.sum(y==0)}, Present(1)={np.sum(y==1)}", flush=True)

    # Split train / validation (80 / 20)
    indices = np.arange(len(X))
    np.random.seed(42)
    np.random.shuffle(indices)
    
    split = int(0.8 * len(X))
    train_idx, val_idx = indices[:split], indices[split:]

    X_train, y_train = torch.tensor(X[train_idx]), torch.tensor(y[train_idx])
    X_val, y_val     = torch.tensor(X[val_idx]), torch.tensor(y[val_idx])

    train_ds = TensorDataset(X_train, y_train)
    val_ds   = TensorDataset(X_val, y_val)

    train_loader = DataLoader(train_ds, batch_size=256, shuffle=True)
    val_loader   = DataLoader(val_ds, batch_size=512, shuffle=False)

    model = create_model()
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=1e-3, weight_decay=1e-4)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)

    epochs = 10
    print(f"[Train] Training on device: {device} for {epochs} epochs...", flush=True)

    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0
        correct = 0
        total = 0

        for bx, by in train_loader:
            bx, by = bx.to(device), by.to(device)
            optimizer.zero_grad()
            logits = model(bx)
            loss = criterion(logits, by)
            loss.backward()
            optimizer.step()

            total_loss += loss.item() * len(by)
            preds = torch.argmax(logits, dim=1)
            correct += (preds == by).sum().item()
            total += len(by)

        train_acc = correct / total
        train_loss = total_loss / total

        # Validation
        model.eval()
        val_loss = 0.0
        val_correct = 0
        val_total = 0
        with torch.no_grad():
            for bx, by in val_loader:
                bx, by = bx.to(device), by.to(device)
                logits = model(bx)
                loss = criterion(logits, by)
                val_loss += loss.item() * len(by)
                preds = torch.argmax(logits, dim=1)
                val_correct += (preds == by).sum().item()
                val_total += len(by)

        val_acc = val_correct / val_total

        print(f"Epoch [{epoch:02d}/{epochs:02d}] - Train Loss: {train_loss:.4f} Acc: {train_acc*100:.1f}% | Val Loss: {val_loss/val_total:.4f} Acc: {val_acc*100:.1f}%", flush=True)

    os.makedirs(os.path.dirname(MODEL_SAVE_PATH), exist_ok=True)
    torch.save(model.state_dict(), MODEL_SAVE_PATH)
    print(f"[Train] ✅ CNN+LSTM Model saved successfully to {MODEL_SAVE_PATH}", flush=True)
    return True

if __name__ == "__main__":
    train()
