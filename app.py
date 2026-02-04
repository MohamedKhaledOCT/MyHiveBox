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

app = Flask(__name__)
metrics = PrometheusMetrics(app)

# --- Configuration ---
VERSION = "v0.0.2"
SENSEBOX_IDS = os.environ.get("SENSEBOX_IDS", "5eba5fbad46fb8001b799786,5c21ff8f919bf8001adf2488,5ade1acf223bd80019a1011c").split(',')

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
    cache = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
except Exception as e:
    cache = None
    print(f"Warning: Redis not connected: {e}")

try:
    s3_client = boto3.client(
        's3',
        endpoint_url=f"http://{MINIO_ENDPOINT}",
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY
    )
except Exception as e:
    s3_client = None
    print(f"Warning: MinIO not connected: {e}")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def fetch_temperature(box_id):
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
    if cache:
        try:
            cached_avg = cache.get("avg_temp")
            if cached_avg: return float(cached_avg)
        except: pass

    temps = [t for t in (fetch_temperature(bid) for bid in SENSEBOX_IDS) if t is not None]
    if not temps: return None
    avg = sum(temps) / len(temps)
    
    if cache:
        try: cache.setex("avg_temp", 300, avg)
        except: pass
    return avg

def save_to_minio():
    avg = get_average_temperature()
    if avg is None or not s3_client: return
    
    data = {"timestamp": time.time(), "temperature": avg, "version": VERSION}
    file_name = f"data_{int(time.time())}.json"
    
    try:
        s3_client.put_object(Bucket=MINIO_BUCKET, Key=file_name, Body=json.dumps(data))
        logger.info(f"Saved {file_name} to MinIO")
    except Exception as e:
        logger.error(f"Failed to save to MinIO: {e}")

# Background Job
def background_store_job():
    while True:
        time.sleep(300)
        with app.app_context(): save_to_minio()

threading.Thread(target=background_store_job, daemon=True).start()

# --- Endpoints ---
@app.route('/version')
def version():
    return jsonify({"version": VERSION})

@app.route('/temperature')
def temperature():
    avg = get_average_temperature()
    if avg is None: return jsonify({"error": "No data"}), 503
    return jsonify({"average_temperature": round(avg, 2), "cached": cache.exists("avg_temp")==1 if cache else False})

@app.route('/store')
def store_endpoint():
    save_to_minio()
    return jsonify({"message": "Data stored successfully"})

@app.route('/readyz')
def readyz():
    try:
        if cache: cache.ping()
        if s3_client: s3_client.list_buckets()
        return jsonify({"status": "ready"}), 200
    except Exception as e:
        return jsonify({"status": "not ready", "error": str(e)}), 503

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
