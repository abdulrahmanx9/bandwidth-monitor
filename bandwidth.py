# bandwidth_monitor.py
#
# A lightweight FastAPI endpoint to monitor and report the average
# network bandwidth usage on a server.
#
# This version automatically detects the correct network interface
# and uses an O(1) running average for high performance.
#
# To run this:
# 1. Install dependencies: pip install "fastapi[all]" psutil
# 2. Run the server: uvicorn bandwidth_monitor:app --host 0.0.0.0 --port 8000
# 3. Access the endpoint at: http://<your_server_ip>:8000/api/v1/stats/bandwidth

import fastapi
import uvicorn
import psutil
import asyncio
import time
import socket
import threading  # --- OPTIMIZATION 1: Import for thread-safety
from collections import deque
from typing import Deque


# --- HELPER FUNCTION ---
def get_default_interface_name() -> str:
    """
    Determines the network interface used for the default route (i.e., to the internet).
    This is the most reliable way to find the primary interface for monitoring.
    """
    print("Attempting to automatically determine the default network interface...")
    try:
        # Create a dummy UDP socket to connect to a public IP address.
        # This doesn't send any data but forces the OS to select the correct
        # interface for outbound traffic based on its routing table.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            local_ip_address = s.getsockname()[0]

        # Now, iterate through all network interfaces on the system.
        for interface_name, snic_addrs in psutil.net_if_addrs().items():
            # For each interface, check all its assigned addresses.
            for snic_addr in snic_addrs:
                # We are looking for the interface that has the IP address we just found.
                if (
                    snic_addr.family == socket.AF_INET
                    and snic_addr.address == local_ip_address
                ):
                    print(
                        f"✅ Successfully determined default network interface: '{interface_name}'"
                    )
                    return interface_name
    except Exception as e:
        print(
            f"⚠️ Warning: Could not determine default interface via routing. Error: {e}"
        )

    # --- Fallback Method ---
    # If the routing method fails, try to find the first non-loopback interface
    # that has a valid IPv4 address. This is less precise but works for many simple setups.
    try:
        print("Attempting fallback method to find a suitable interface...")
        all_interfaces = psutil.net_if_addrs()
        for interface_name, addrs in all_interfaces.items():
            # Ignore the loopback interface ('lo')
            if "lo" in interface_name.lower():
                continue
            # Check if any address on this interface is an IPv4 address
            if any(addr.family == socket.AF_INET for addr in addrs):
                print(
                    f"✅ Found non-loopback interface as fallback: '{interface_name}'"
                )
                return interface_name
    except Exception as e:
        print(f"❌ Fallback method also failed. Error: {e}")

    # Final, last-resort fallback to a common default name.
    print("❌ All detection methods failed. Falling back to 'eth0'.")
    return "eth0"


# --- CONFIGURATION ---
# The script now calls the helper function on startup to set this variable.
NETWORK_INTERFACE = get_default_interface_name()

# How often to sample the network usage, in seconds.
SAMPLE_INTERVAL_SECONDS = 5

# The number of samples to keep to calculate the average over a time period.
# Example: (6 hours * 60 minutes/hour * 60 seconds/minute) / 5 seconds/sample = 4320 samples
MAX_SAMPLES = (12 * 60 * 60) // SAMPLE_INTERVAL_SECONDS
# --- END CONFIGURATION ---


# --- GLOBAL STATE ---
# --- OPTIMIZATION 1: Use a standard deque and add a running total
bandwidth_samples: Deque[float] = deque()
running_total: float = 0.0
GLOBAL_LOCK = threading.Lock()
# ---
app = fastapi.FastAPI()


# --- BACKGROUND TASK ---
async def monitor_bandwidth():
    """
    This is a background task that runs continuously. It samples network usage
    at a set interval and updates the global 'bandwidth_samples' deque
    and 'running_total' for an O(1) average calculation.
    """
    global running_total  # Allow modification of the global variable

    # --- OPTIMIZATION 4: Simplified initial read
    try:
        net_io_initial = psutil.net_io_counters(pernic=True).get(
            NETWORK_INTERFACE, psutil.net_io_counters()
        )
        last_bytes_sent = net_io_initial.bytes_sent
    except Exception as e:
        print(
            f"❌ Fatal Error: Could not get initial network stats. Exiting monitor task. Error: {e}"
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
            current_bytes_sent = net_io.bytes_sent
            bytes_delta = current_bytes_sent - last_bytes_sent

            if time_delta > 0:
                # --- OPTIMIZATION 3: Use 1_000_000 for Megabits (Mbps)
                # Calculate speed in Megabits per second (Mbps)
                # (bytes * 8 bits/byte) / 1,000,000 bits/megabit / seconds
                speed_mbps = (bytes_delta * 8) / 1_000_000 / time_delta
                # ---

                # --- OPTIMIZATION 1: Update running total
                with GLOBAL_LOCK:
                    bandwidth_samples.append(speed_mbps)
                    running_total += speed_mbps

                    # If deque is over size, remove the oldest and subtract from total
                    if len(bandwidth_samples) > MAX_SAMPLES:
                        oldest_sample = bandwidth_samples.popleft()
                        running_total -= oldest_sample
                # ---

            last_bytes_sent = current_bytes_sent
            last_check_time = current_time
        except Exception as e:
            print(f"Error during network stats collection: {e}")


# --- API ENDPOINT ---
@app.get("/api/v1/stats/bandwidth")
def get_average_bandwidth():
    """
    Calculates and returns the average bandwidth usage based on collected samples.
    This is now an O(1) operation, as it just reads the pre-calculated state.
    """
    # --- OPTIMIZATION 2: Use lock for thread-safe read
    with GLOBAL_LOCK:
        if not bandwidth_samples:
            avg_speed = 0.0
            current_count = 0
        else:
            current_count = len(bandwidth_samples)
            avg_speed = running_total / current_count
    # ---

    return {
        "network_interface": NETWORK_INTERFACE,
        "average_speed": {
            "value": round(avg_speed, 2),
            "unit": "mbps",
            "period_seconds": MAX_SAMPLES * SAMPLE_INTERVAL_SECONDS,
        },
        "current_sample_count": current_count,
        "max_samples_for_avg": MAX_SAMPLES,
    }


# --- FASTAPI LIFECYCLE ---
@app.on_event("startup")
async def startup_event():
    """
    Starts the background monitoring task when the server starts.
    """
    print("Server starting up...")
    asyncio.create_task(monitor_bandwidth())


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
