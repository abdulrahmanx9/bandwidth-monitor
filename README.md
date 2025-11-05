# FastAPI Bandwidth Monitor

A lightweight FastAPI application to monitor network bandwidth speeds and track persistent, monthly data usage. All endpoints are secured with an API key.

## Features

* **Real-time Speed:** Calculates the average upload and download speed (in Mbps) over a configurable time window (default: 12 hours).
* **Monthly Data Tracking:** Tracks the total bytes sent and received for the current month.
* **Data Persistence:** Automatically saves monthly traffic data to `monthly_traffic.json` every 5 minutes and reloads it on startup.
* **Secure Endpoints:** All API endpoints are protected and require an `X-API-Key` header for access.
* **Auto-Detection:** Automatically detects the default network interface used for internet traffic.

## Setup & Installation

1.  **Clone the repository (or download the script):**
    ```bash
    git clone [https://github.com/YourUsername/fastapi-bandwidth-monitor.git](https://github.com/YourUsername/fastapi-bandwidth-monitor.git)
    cd fastapi-bandwidth-monitor
    ```

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Set the API Key:**
    The application reads the API key from an environment variable. Set it in your terminal:
    ```bash
    export BANDWIDTH_API_KEY="your-super-secret-key-here"
    ```
    *(On Windows, use `set BANDWIDTH_API_KEY="your-super-secret-key-here"`)*

## Running the Application

Use `uvicorn` to run the server. It's recommended to bind to `0.0.0.0` to make it accessible on your network.

```bash
uvicorn bandwidth_monitor:app --host 0.0.0.0 --port 8000
````

The server will start, load any existing traffic data, and begin monitoring.

## API Endpoints

All endpoints require the `X-API-Key` header you set in the environment variable.

-----

### 1\. Get Average Bandwidth

Provides the average network speed for the configured time window.

  * **Endpoint:** `GET /api/v1/stats/bandwidth`
  * **Example Request (`curl`):**
    ```bash
    curl -X GET "[http://127.0.0.1:8000/api/v1/stats/bandwidth](http://127.0.0.1:8000/api/v1/stats/bandwidth)" \
         -H "X-API-Key: your-super-secret-key-here"
    ```
  * **Example Response:**
    ```json
    {
      "network_interface": "eth0",
      "average_speed_mbps": {
        "sent": 15.82,
        "received": 120.45,
        "total": 136.27
      },
      "period_seconds": 43200,
      "current_sample_count": 8640,
      "max_samples_for_avg": 8640
    }
    ```

-----

### 2\. Get Monthly Traffic

Provides the total data usage tracked for the current month.

  * **Endpoint:** `GET /api/v1/stats/monthly-traffic`
  * **Example Request (`curl`):**
    ```bash
    curl -X GET "[http://127.0.0.1:8000/api/v1/stats/monthly-traffic](http://127.0.0.1:8000/api/v1/stats/monthly-traffic)" \
         -H "X-API-Key: your-super-secret-key-here"
    ```
  * **Example Response:**
    ```json
    {
      "month": "2025-11",
      "data_usage": {
        "sent": "12.51 GB",
        "received": "102.78 GB",
        "total": "115.29 GB"
      },
      "raw_bytes": {
        "sent": 13430988800,
        "received": 110359838720,
        "total": 123790827520
      }
    }
    ```