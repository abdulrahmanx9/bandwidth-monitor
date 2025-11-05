import os
import json
import fastapi
import uvicorn
import psutil
import asyncio
import time
import socket
import threading
import logging
from collections import deque
from typing import Deque, Dict, Any
from pathlib import Path
from datetime import datetime
from fastapi import Depends, HTTPException, status
from fastapi.security import APIKeyHeader

# --- IMPROVEMENT: Basic logging configuration ---
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)

# Configuration
SAMPLE_INTERVAL_SECONDS = 5
MAX_SAMPLES = (12 * 60 * 60) // SAMPLE_INTERVAL_SECONDS
PERSISTENCE_FILE = Path("monthly_traffic.json")
SAVE_INTERVAL_MINUTES = 5

# API Key Setup
API_KEY = os.getenv("BANDWIDTH_API_KEY", "insecure-default-key-change-me")
if API_KEY == "insecure-default-key-change-me":
    logging.warning(
        "You are using a default, insecure API key. Please set BANDWIDTH_API_KEY."
    )

api_key_header_scheme = APIKeyHeader(name="X-API-Key")


async def get_api_key(api_key: str = Depends(api_key_header_scheme)):
    if api_key != API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or Missing API Key",
        )


# Helper Functions
def get_default_interface_name() -> str:
    logging.info(
        "Attempting to automatically determine the default network interface..."
    )
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip_address = s.getsockname()[0]
        for interface_name, snic_addrs in psutil.net_if_addrs().items():
            for snic_addr in snic_addrs:
                if (
                    snic_addr.family == socket.AF_INET
                    and snic_addr.address == local_ip_address
                ):
                    logging.info(
                        f"✅ Successfully determined default network interface: '{interface_name}'"
                    )
                    return interface_name
    except Exception as e:
        logging.warning(
            f"Could not determine default interface, falling back to 'eth0'. Error: {e}"
        )
        return "eth0"


def format_bytes(byte_count: int) -> str:
    if byte_count is None:
        return "0 B"
    power = 1024
    n = 0
    power_labels = {0: "", 1: "K", 2: "M", 3: "G", 4: "T"}
    while byte_count >= power and n < len(power_labels) - 1:
        byte_count /= power
        n += 1
    return f"{byte_count:.2f} {power_labels[n]}B"


# Global State
NETWORK_INTERFACE = get_default_interface_name()
sent_samples: Deque[float] = deque()
recv_samples: Deque[float] = deque()
running_total_sent: float = 0.0
running_total_recv: float = 0.0
monthly_traffic_state: Dict[str, Any] = {}
GLOBAL_LOCK = threading.Lock()
app = fastapi.FastAPI()


# Persistence Functions
def load_monthly_traffic():
    global monthly_traffic_state
    current_month = datetime.now().strftime("%Y-%m")
    if PERSISTENCE_FILE.exists():
        try:
            with open(PERSISTENCE_FILE, "r") as f:
                data = json.load(f)
            if data.get("month") == current_month:
                monthly_traffic_state = data
                logging.info(f"✅ Loaded traffic data for month {current_month}.")
                return
        except (json.JSONDecodeError, IOError) as e:
            logging.error(
                f"Could not read persistence file. Starting fresh. Error: {e}"
            )

    logging.info(f"✨ Initializing new traffic log for month {current_month}.")
    monthly_traffic_state = {
        "month": current_month,
        "total_bytes_sent": 0,
        "total_bytes_recv": 0,
    }


async def save_monthly_traffic_periodically():
    while True:
        await asyncio.sleep(SAVE_INTERVAL_MINUTES * 60)
        with GLOBAL_LOCK:
            state_to_save = monthly_traffic_state.copy()
        try:
            with open(PERSISTENCE_FILE, "w") as f:
                json.dump(state_to_save, f, indent=4)
            logging.info("💾 Persisted monthly traffic data.")
        except IOError as e:
            logging.error(f"❌ Error saving persistence file: {e}")


# Background Tasks
async def monitor_bandwidth():
    global running_total_sent, running_total_recv, monthly_traffic_state
    try:
        net_io_initial = psutil.net_io_counters(pernic=True).get(
            NETWORK_INTERFACE, psutil.net_io_counters()
        )
        last_bytes_sent = net_io_initial.bytes_sent
        last_bytes_recv = net_io_initial.bytes_recv
    except Exception as e:
        logging.error(
            f"❌ FATAL: Could not get initial network stats. Monitoring task will not run. Error: {e}"
        )
        return

    last_check_time = time.time()
    while True:
        await asyncio.sleep(SAMPLE_INTERVAL_SECONDS)
        current_time = time.time()
        time_delta = current_time - last_check_time
        try:
            net_io = psutil.net_io_counters(pernic=True).get(
                NETWORK_INTERFACE, psutil.net_io_counters()
            )
            bytes_sent_delta = net_io.bytes_sent - last_bytes_sent
            bytes_recv_delta = net_io.bytes_recv - last_bytes_recv
            if time_delta > 0:
                speed_sent_mbps = (bytes_sent_delta * 8) / 1_000_000 / time_delta
                speed_recv_mbps = (bytes_recv_delta * 8) / 1_000_000 / time_delta
                with GLOBAL_LOCK:
                    sent_samples.append(speed_sent_mbps)
                    running_total_sent += speed_sent_mbps
                    if len(sent_samples) > MAX_SAMPLES:
                        running_total_sent -= sent_samples.popleft()

                    recv_samples.append(speed_recv_mbps)
                    running_total_recv += speed_recv_mbps
                    if len(recv_samples) > MAX_SAMPLES:
                        running_total_recv -= recv_samples.popleft()

                    current_month = datetime.now().strftime("%Y-%m")
                    if monthly_traffic_state.get("month") != current_month:
                        logging.info(
                            f"🎉 Month rolled over to {current_month}. Resetting monthly traffic."
                        )
                        monthly_traffic_state = {
                            "month": current_month,
                            "total_bytes_sent": 0,
                            "total_bytes_recv": 0,
                        }
                    monthly_traffic_state["total_bytes_sent"] += bytes_sent_delta
                    monthly_traffic_state["total_bytes_recv"] += bytes_recv_delta

            last_bytes_sent = net_io.bytes_sent
            last_bytes_recv = net_io.bytes_recv
            last_check_time = current_time
        except Exception as e:
            logging.error(f"Error during network stats collection: {e}")


# API Endpoints
@app.get("/api/v1/stats/bandwidth", dependencies=[Depends(get_api_key)])
def get_bandwidth_stats():
    with GLOBAL_LOCK:
        if not sent_samples:
            avg_sent, avg_recv, current_count = 0.0, 0.0, 0
        else:
            current_count = len(sent_samples)
            avg_sent = running_total_sent / current_count
            avg_recv = running_total_recv / current_count
    return {
        "network_interface": NETWORK_INTERFACE,
        "average_speed_mbps": {
            "sent": round(avg_sent, 2),
            "received": round(avg_recv, 2),
            "total": round(avg_sent + avg_recv, 2),
        },
        "period_seconds": MAX_SAMPLES * SAMPLE_INTERVAL_SECONDS,
        "current_sample_count": current_count,
        "max_samples_for_avg": MAX_SAMPLES,
    }


@app.get("/api/v1/stats/monthly-traffic", dependencies=[Depends(get_api_key)])
def get_monthly_traffic():
    with GLOBAL_LOCK:
        state = monthly_traffic_state.copy()
    total_bytes = state.get("total_bytes_sent", 0) + state.get("total_bytes_recv", 0)
    return {
        "month": state.get("month"),
        "data_usage": {
            "sent": format_bytes(state.get("total_bytes_sent", 0)),
            "received": format_bytes(state.get("total_bytes_recv", 0)),
            "total": format_bytes(total_bytes),
        },
        "raw_bytes": {
            "sent": state.get("total_bytes_sent", 0),
            "received": state.get("total_bytes_recv", 0),
            "total": total_bytes,
        },
    }


# FastAPI Lifecycle
@app.on_event("startup")
async def startup_event():
    logging.info("🚀 Server starting up...")
    load_monthly_traffic()
    asyncio.create_task(monitor_bandwidth())
    asyncio.create_task(save_monthly_traffic_periodically())


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
