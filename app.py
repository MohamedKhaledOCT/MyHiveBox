"""
HiveBox Main Application.
Connects to OpenSenseMap, Redis, and MinIO.
"""
import os
import time
import json
import logging
import threading
import requests
import redis
import boto3
from flask import Flask, jsonify
from prometheus_flask_exporter import PrometheusMetrics

# pylint: disable=broad-exception-caught, bare-except, invalid-name

app = Flask(__name__)
metrics = PrometheusMetrics(app)

# --- Configuration ---
VERSION = "v0.0.2"
SENSEBOX_IDS = os.environ.get(
    "SENSEBOX_IDS",
    "5eba5fbad46fb8001b799786,5c21ff8f919bf8001adf2488,5ade1acf223bd80019a1011c"
).split(',')

# Redis Config
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", 6379))

# MinIO Config
MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "admin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "password")
MINIO_BUCKET = os.environ.get("MINIO_BUCKET", "hivebox-data")

# --- Initialize Clients ---
try:
    CACHE = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
except Exception as e:
    CACHE = None
    print(f"Warning: Redis not connected: {e}")

try:
    S3_CLIENT = boto3.client(
        's3',
        endpoint_url=f"http://{MINIO_ENDPOINT}",
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY
    )
except Exception as e:
    S3_CLIENT = None
    print(f"Warning: MinIO not connected: {e}")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fetch_temperature(box_id):
    """Fetch temperature from a single SenseBox."""
    url = f"https://api.opensensemap.org/boxes/{box_id}"
    try:
        response = requests.get(url, timeout=5)
        response.raise_for_status()
        data = response.json()
        for sensor in data.get('sensors', []):
            if sensor.get('title') == 'Temperatur' or sensor.get('unit') == '°C':
                return float(sensor.get('lastMeasurement', {}).get('value'))
    except Exception:
        pass
    return None

def get_average_temperature():
    """Calculate average temperature from Cache or API."""
    # 1. Try Cache
    if CACHE:
        try:
            cached_avg = CACHE.get("avg_temp")
            if cached_avg:
                return float(cached_avg)
        except Exception:
            pass

    # 2. Fetch Fresh
    temps = [t for t in (fetch_temperature(bid) for bid in SENSEBOX_IDS) if t is not None]

    if not temps:
        return None

    avg = sum(temps) / len(temps)

    # 3. Store in Cache
    if CACHE:
        try:
            CACHE.setex("avg_temp", 300, avg)
        except Exception:
            pass
    return avg

def save_to_minio():
    """Save the current average temperature to MinIO storage."""
    avg = get_average_temperature()
    if avg is None or not S3_CLIENT:
        return

    data = {
        "timestamp": time.time(),
        "temperature": avg,
        "version": VERSION
    }
    file_name = f"data_{int(time.time())}.json"

    try:
        S3_CLIENT.put_object(
            Bucket=MINIO_BUCKET,
            Key=file_name,
            Body=json.dumps(data)
        )
        logger.info("Saved %s to MinIO", file_name)
    except Exception as e:
        logger.error("Failed to save to MinIO: %s", e)

# Background Job
def background_store_job():
    """Periodic job to save data."""
    while True:
        time.sleep(300)
        with app.app_context():
            save_to_minio()

threading.Thread(target=background_store_job, daemon=True).start()

# --- Endpoints ---
@app.route('/version')
def version():
    """Return app version."""
    return jsonify({"version": VERSION})

@app.route('/temperature')
def temperature():
    """Return average temperature and status."""
    avg = get_average_temperature()
    if avg is None:
        return jsonify({"error": "No data"}), 503

    status = "Good"
    if avg < 10:
        status = "Too Cold"
    elif avg > 36:
        status = "Too Hot"

    cached_status = False
    if CACHE:
        cached_status = CACHE.exists("avg_temp") == 1

    return jsonify({
        "average_temperature": round(avg, 2),
        "status": status,
        "cached": cached_status
    })

@app.route('/store')
def store_endpoint():
    """Manually trigger storage."""
    save_to_minio()
    return jsonify({"message": "Data stored successfully"})

@app.route('/readyz')
def readyz():
    """Health check endpoint."""
    try:
        if CACHE:
            CACHE.ping()
        if S3_CLIENT:
            S3_CLIENT.list_buckets()
        return jsonify({"status": "ready"}), 200
    except Exception as e:
        return jsonify({"status": "not ready", "error": str(e)}), 503

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
