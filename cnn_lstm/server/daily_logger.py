"""
Daily 24/7 Presence Logger & Excel/CSV Summary Generator for WaveSense.

Maintains continuous 24/7 logging into daily CSV files in `reports/`.
Automatically compiles 24-hour hourly occupancy summaries at midnight.
"""

import os
import csv
import time
from datetime import datetime, date, timedelta
import threading

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_DIR = os.path.join(BASE_DIR, "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)


class DailyPresenceLogger:
    def __init__(self, reports_dir=REPORTS_DIR):
        self.reports_dir = reports_dir
        os.makedirs(self.reports_dir, exist_ok=True)
        self.lock = threading.Lock()
        self.last_log_time = 0
        self.LOG_INTERVAL_SEC = 2.0  # Log sample every 2 seconds to avoid excessive disk growth

    def _get_raw_csv_path(self, target_date=None):
        if target_date is None:
            target_date = date.today()
        date_str = target_date.strftime("%Y-%m-%d")
        return os.path.join(self.reports_dir, f"presence_log_{date_str}.csv")

    def _get_summary_csv_path(self, target_date=None):
        if target_date is None:
            target_date = date.today()
        date_str = target_date.strftime("%Y-%m-%d")
        return os.path.join(self.reports_dir, f"Daily_Summary_{date_str}.csv")

    def log_inference(self, state, confidence, rssi=-65, motion_var=0.0, restricted_alert=False):
        now_ts = time.time()
        if now_ts - self.last_log_time < self.LOG_INTERVAL_SEC:
            return

        self.last_log_time = now_ts
        now_dt = datetime.now()
        raw_path = self._get_raw_csv_path(now_dt.date())

        file_exists = os.path.exists(raw_path)

        with self.lock:
            try:
                with open(raw_path, mode="a", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    if not file_exists:
                        writer.writerow([
                            "Timestamp", "Time", "State", "Confidence (%)", 
                            "RSSI (dBm)", "Subcarrier Motion Variance", "Security Alert"
                        ])

                    state_str = "Present" if state == 1 else "Empty"
                    alert_str = "YES" if restricted_alert else "NO"
                    time_str = now_dt.strftime("%H:%M:%S")
                    ts_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")

                    writer.writerow([
                        ts_str, time_str, state_str, round(confidence, 1),
                        round(rssi, 1), round(motion_var, 3), alert_str
                    ])
            except Exception as e:
                print(f"[DailyLogger] Error writing log: {e}")

    def generate_daily_summary(self, target_date=None):
        if target_date is None:
            target_date = date.today()

        raw_path = self._get_raw_csv_path(target_date)
        summary_path = self._get_summary_csv_path(target_date)

        if not os.path.exists(raw_path):
            print(f"[DailyLogger] No raw log file found for {target_date}")
            return None

        hourly_data = {h: {"total": 0, "present": 0, "empty": 0, "alerts": 0, "conf_sum": 0.0} for h in range(24)}

        with self.lock:
            try:
                with open(raw_path, mode="r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        try:
                            ts = datetime.strptime(row["Timestamp"], "%Y-%m-%d %H:%M:%S")
                            h = ts.hour
                            state = row.get("State", "Empty")
                            conf = float(row.get("Confidence (%)", 0))
                            alert = row.get("Security Alert", "NO")

                            hourly_data[h]["total"] += 1
                            if state == "Present":
                                hourly_data[h]["present"] += 1
                            else:
                                hourly_data[h]["empty"] += 1

                            if alert == "YES":
                                hourly_data[h]["alerts"] += 1

                            hourly_data[h]["conf_sum"] += conf
                        except Exception:
                            continue

                with open(summary_path, mode="w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "Hour Window", "Total Samples", "Present Duration (Min)", 
                        "Empty Duration (Min)", "Occupancy Rate (%)", 
                        "Average Confidence (%)", "Security Alerts Count"
                    ])

                    total_present_min = 0
                    total_empty_min = 0
                    total_alerts = 0

                    for h in range(24):
                        d = hourly_data[h]
                        t = d["total"]
                        if t > 0:
                            occ_rate = round((d["present"] / t) * 100, 1)
                            avg_conf = round(d["conf_sum"] / t, 1)
                            # Log interval is 2 sec -> 30 samples = 1 minute
                            pres_min = round(d["present"] * 2 / 60, 1)
                            emp_min = round(d["empty"] * 2 / 60, 1)
                        else:
                            occ_rate = 0.0
                            avg_conf = 0.0
                            pres_min = 0.0
                            emp_min = 0.0

                        total_present_min += pres_min
                        total_empty_min += emp_min
                        total_alerts += d["alerts"]

                        hour_str = f"{h:02d}:00 - {h:02d}:59"
                        writer.writerow([
                            hour_str, t, pres_min, emp_min, f"{occ_rate}%", f"{avg_conf}%", d["alerts"]
                        ])

                    writer.writerow([])
                    writer.writerow([
                        "24-HOUR TOTAL SUMMARY", "", round(total_present_min, 1), 
                        round(total_empty_min, 1), 
                        f"{round((total_present_min / max(1, total_present_min + total_empty_min))*100, 1)}%", 
                        "", total_alerts
                    ])

                print(f"[DailyLogger] Generated 24-hour summary report: {summary_path}")
                return summary_path
            except Exception as e:
                print(f"[DailyLogger] Error generating summary: {e}")
                return None

    def list_reports(self):
        reports = []
        if not os.path.exists(self.reports_dir):
            return reports

        for fname in sorted(os.listdir(self.reports_dir), reverse=True):
            if fname.endswith(".csv"):
                fpath = os.path.join(self.reports_dir, fname)
                size_kb = round(os.path.getsize(fpath) / 1024, 1)
                mod_time = datetime.fromtimestamp(os.path.getmtime(fpath)).strftime("%Y-%m-%d %H:%M:%S")
                reports.append({
                    "filename": fname,
                    "size_kb": size_kb,
                    "modified": mod_time,
                    "type": "24-Hour Summary" if "Daily_Summary" in fname else "Raw Logs"
                })
        return reports
