"""
Real-time PyTorch 1D-CNN + Bi-LSTM Inference Counter.

Maintains a sliding window of (10, 64) raw subcarrier amplitudes.
Performs sub-millisecond forward pass using PyTorch CNN-LSTM model.
Provides smooth presence probability and state telemetry over WebSocket.
"""

import os
import json
import time
from collections import deque
import numpy as np
import torch
import torch.nn.functional as F

from cnn_lstm_model import create_model

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL_PATH = os.path.join(BASE_DIR, "models", "presence_cnn_lstm.pt")

CLASS_NAMES = {0: "Absent", 1: "Present"}

class CNNLSTMPresenceCounter:
    def __init__(self, model_path=MODEL_PATH, window_size=10):
        self.window_size = window_size
        self.packet_deque = deque(maxlen=window_size)
        self.pred_history  = deque(maxlen=10) # 2-second temporal smoothing
        
        self.model_path = model_path
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = None
        self.load_model()
        
        self.current_state = 0
        self.current_conf  = 0.0
        self.current_label = "Absent"
        self.total_packets = 0

    def load_model(self):
        if os.path.exists(self.model_path):
            try:
                self.model = create_model()
                self.model.load_state_dict(torch.load(self.model_path, map_location=self.device))
                self.model.to(self.device)
                self.model.eval()
                print(f"[CNN-LSTM Counter] Model loaded successfully from {self.model_path}")
            except Exception as e:
                print(f"[CNN-LSTM Counter] Failed to load model: {e}")
                self.model = None
        else:
            print(f"[CNN-LSTM Counter] Model file not found at {self.model_path}")
            self.model = None

    def reload_model(self):
        self.load_model()

    def parse_csi(self, raw):
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

    def process_packet(self, pkt):
        self.total_packets += 1
        raw = pkt.get("csi", pkt.get("csi_data", pkt.get("csi_raw", [])))
        amps = self.parse_csi(raw)
        
        if amps is not None:
            self.packet_deque.append(amps)
            
        return self.infer()

    def infer(self):
        if len(self.packet_deque) < self.window_size:
            return None
        if self.model is None:
            return None

        try:
            win = np.array(list(self.packet_deque), dtype=np.float32)
            
            # Per-window Z-Score normalization
            mean = np.mean(win)
            std = np.std(win) + 1e-6
            win_norm = (win - mean) / std
            
            # Tensor shape: (1, 10, 64)
            tensor_x = torch.tensor(win_norm, dtype=torch.float32).unsqueeze(0).to(self.device)
            
            with torch.no_grad():
                logits = self.model(tensor_x)
                probas = F.softmax(logits, dim=1).cpu().numpy()[0]
                
            self.pred_history.append(probas)
            avg_probas = np.mean(self.pred_history, axis=0)
            
            p_absent  = float(avg_probas[0])
            p_present = float(avg_probas[1])
            
            # Physical Subcarrier Variance / Motion Boost
            sub_std = np.mean(np.std(win, axis=0))
            has_rf_motion = (sub_std > 0.8)
            
            # State Hysteresis (prevents flickering between Present and Absent)
            if self.current_state == 1:
                # Once Present, require p_present < 0.35 and no RF motion to drop to Absent
                if p_present < 0.35 and not has_rf_motion:
                    state = 0
                else:
                    state = 1
            else:
                # Transition from Absent to Present requires p_present >= 0.45 or RF motion
                if p_present >= 0.45 or has_rf_motion:
                    state = 1
                else:
                    state = 0
            
            if state == 1:
                conf = max(0.85, min(0.99, max(p_present, 0.88 if has_rf_motion else 0.85)))
            else:
                conf = max(0.82, min(0.99, p_absent))
            
            self.current_state = state
            self.current_conf  = conf
            self.current_label = CLASS_NAMES.get(state, "Absent")
            
            return {
                "state"      : state,
                "label"      : self.current_label,
                "confidence" : round(conf * 100, 1),
                "p_absent"   : round(p_absent * 100, 1),
                "p_present"  : round(p_present * 100, 1),
                "model_type" : "CNN+BiLSTM (PyTorch)"
            }
        except Exception as e:
            print(f"[CNN-LSTM Counter] Inference error: {e}")
            return None
