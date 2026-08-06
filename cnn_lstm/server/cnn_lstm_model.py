"""
1D-CNN + LSTM Deep Learning Architecture for Wi-Fi CSI Presence Detection.

Input Shape: (Batch_Size, Seq_Len=10, Subcarriers=64)
- 1D-CNN: Extracts spatial cross-subcarrier feature maps per time step.
- Bi-LSTM: Models temporal evolution of subcarrier phase/amplitude across time frames.
- Dense Classifier: Outputs binary probability for Presence / Absence.
"""

import torch
import torch.nn as nn
import torch.nn.functional as F

class CSI_CNN_LSTM(nn.Module):
    def __init__(self, seq_len=10, num_subcarriers=64, hidden_size=64, num_classes=2):
        super(CSI_CNN_LSTM, self).__init__()
        self.seq_len = seq_len
        self.num_subcarriers = num_subcarriers
        
        # ── 1. Spatial 1D-CNN Feature Extractor ──
        # Input per timestep: (1, 64) -> Output: (32, 32)
        self.conv1 = nn.Conv1d(in_channels=1, out_channels=32, kernel_size=5, padding=2)
        self.bn1   = nn.BatchNorm1d(32)
        self.pool1 = nn.MaxPool1d(kernel_size=2, stride=2)
        
        # Input: (32, 32) -> Output: (64, 16)
        self.conv2 = nn.Conv1d(in_channels=32, out_channels=64, kernel_size=3, padding=1)
        self.bn2   = nn.BatchNorm1d(64)
        self.pool2 = nn.MaxPool1d(kernel_size=2, stride=2)
        
        # Spatial feature size per frame = 64 * 16 = 1024
        self.spatial_feature_dim = 64 * 16
        
        # ── 2. Temporal Bi-LSTM Layer ──
        self.lstm = nn.LSTM(
            input_size=self.spatial_feature_dim,
            hidden_size=hidden_size,
            num_layers=2,
            batch_first=True,
            bidirectional=True,
            dropout=0.2
        )
        
        # ── 3. Classification Head ──
        # Bidirectional LSTM has hidden_size * 2
        self.fc1 = nn.Linear(hidden_size * 2, 32)
        self.dropout = nn.Dropout(0.3)
        self.fc2 = nn.Linear(32, num_classes)

    def forward(self, x):
        # x shape: (B, T, C) where B=batch, T=10, C=64
        B, T, C = x.size()
        
        # Reshape to process each frame through 1D-CNN: (B*T, 1, C)
        x_flat = x.view(B * T, 1, C)
        
        # Conv block 1
        h = self.pool1(F.relu(self.bn1(self.conv1(x_flat))))
        # Conv block 2
        h = self.pool2(F.relu(self.bn2(self.conv2(h))))
        
        # Flatten spatial features per timestep: (B, T, 1024)
        spatial_feats = h.view(B, T, -1)
        
        # Pass through Temporal LSTM: output shape (B, T, hidden_size*2)
        lstm_out, _ = self.lstm(spatial_feats)
        
        # Use final time step hidden state
        final_state = lstm_out[:, -1, :]
        
        # Dense classification head
        out = F.relu(self.fc1(final_state))
        out = self.dropout(out)
        logits = self.fc2(out)
        
        return logits

def create_model():
    return CSI_CNN_LSTM()

if __name__ == "__main__":
    model = create_model()
    dummy_input = torch.randn(8, 10, 64) # Batch of 8 windows
    out = model(dummy_input)
    print(f"CNN-LSTM Model Initialized. Dummy Output Shape: {out.shape}")
