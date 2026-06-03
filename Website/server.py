import os
import requests
from flask import Flask, render_template, Response, jsonify, request, send_file
from flask_socketio import SocketIO
import paho.mqtt.client as mqtt
import json
import threading
import csv
import io
import sqlite3
import re
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from werkzeug.security import check_password_hash
import pytz
import time

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR.parent / "Data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE = DATA_DIR / "measurements.db"
CREDENTIALS = DATA_DIR / "mqtt_credentials.json"
ALERT_SETTINGS_FILE = DATA_DIR / "alert_settings.json"
EMAILJS_CONFIG_FILE = DATA_DIR / "emailjs_config.json"
APP_ORIGIN = os.environ.get("APP_ORIGIN", "").rstrip("/")

SLOVAKIA_TZ = pytz.timezone("Europe/Bratislava")
CLEAR_PASSWORD_HASH = "scrypt:32768:8:1$m17atgQYR6G7OLvX$fa73ef989a842407c1a4bd5b16b5a0a1713574130efa59e5454fe8f31092f4ec66d9d8f65cbc38361fbc231ec5e7917d40914402159f0abc5c382ad94e011902"

DEFAULT_REALTIME_POINTS = 100
MAX_REALTIME_POINTS = 1000
DEFAULT_FILTER_RENDER_LIMIT = 1000
MAX_FILTER_RENDER_LIMIT = 10000

VALID_GAINS = {1, 2, 0.125, 0.25}
VALID_IT_TIMES = {25, 50, 100, 200, 400, 800}
MIN_INTERVAL = 0.25
MAX_INTERVAL = 60
MIN_SAMPLES = 1
MAX_SAMPLES = 10

VALID_ALERT_DIRECTIONS = {"above", "below"}
EMAIL_REGEX = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

DEFAULT_ALERT_SETTINGS = {
    "enabled": False,
    "email": "",
    "threshold": 100.0,
    "direction": "above",
}

ALERT_REQUIRED_CONSECUTIVE_HITS = 3

alert_settings_lock = threading.Lock()
alert_runtime_lock = threading.Lock()
alert_runtime_state = {
    "last_condition": False,
    "consecutive_hits": 0,
    "last_sent_at": None,
}


def get_slovakia_time():
    return datetime.now(SLOVAKIA_TZ)


def load_credentials(path=CREDENTIALS):
    env_username = os.getenv("MQTT_USERNAME", "").strip()
    env_password = os.getenv("MQTT_PASSWORD", "").strip()
    env_broker_ip = os.getenv("MQTT_BROKER_IP", "").strip()
    env_broker_port = os.getenv("MQTT_BROKER_PORT", "").strip()

    if env_username and env_password and env_broker_ip and env_broker_port:
        return (
            env_username,
            env_password,
            env_broker_ip,
            int(env_broker_port),
        )

    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
        return (
            data.get("username"),
            data.get("password"),
            data.get("broker_ip"),
            int(data.get("broker_port")),
        )


def init_db():
    with get_db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS measurements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                lux REAL NOT NULL
            )
            """
        )
        conn.commit()


@contextmanager
def get_db():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def load_json_file(path, default_value):
    try:
        with open(path, "r", encoding="utf-8") as file:
            raw = file.read().strip()
            if not raw:
                return default_value.copy() if isinstance(default_value, dict) else default_value
            data = json.loads(raw)
            if isinstance(default_value, dict) and isinstance(data, dict):
                merged = default_value.copy()
                merged.update(data)
                return merged
            return data
    except (OSError, ValueError, TypeError):
        return default_value.copy() if isinstance(default_value, dict) else default_value


def save_json_file(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)


def init_alert_storage():
    with alert_settings_lock:
        if not ALERT_SETTINGS_FILE.exists():
            save_json_file(ALERT_SETTINGS_FILE, DEFAULT_ALERT_SETTINGS)


def get_alert_settings():
    with alert_settings_lock:
        return load_json_file(ALERT_SETTINGS_FILE, DEFAULT_ALERT_SETTINGS)


def save_alert_settings(settings):
    with alert_settings_lock:
        save_json_file(ALERT_SETTINGS_FILE, settings)


def reset_alert_runtime_state():
    with alert_runtime_lock:
        alert_runtime_state["last_condition"] = False
        alert_runtime_state["consecutive_hits"] = 0
        alert_runtime_state["last_sent_at"] = None


def public_alert_settings(settings):
    return {
        "enabled": bool(settings.get("enabled", False)),
        "email": settings.get("email", ""),
        "threshold": float(settings.get("threshold", 0)),
        "direction": settings.get("direction", "above"),
    }


def parse_request_timestamp(timestamp_value):
    parsed_time = datetime.fromisoformat(timestamp_value.replace("Z", "+00:00"))
    slovakia_time = parsed_time.astimezone(SLOVAKIA_TZ)
    return slovakia_time.strftime("%Y-%m-%dT%H:%M:%S%z")


def fetch_measurements(query, params=None):
    with get_db() as conn:
        rows = conn.execute(query, params or []).fetchall()
    return [{"time": row["timestamp"], "lux": row["lux"]} for row in rows]


def clamp(value, minimum, maximum):
    return max(minimum, min(value, maximum))


def limit_realtime_data(data, max_points):
    if len(data) > max_points:
        return data[-max_points:]
    return data


def optimize_filter_data(data, render_limit):
    if render_limit <= 0 or len(data) <= render_limit:
        return data

    optimized_data = []
    last_index = len(data) - 1
    step = last_index / (render_limit - 1)

    for point_number in range(render_limit):
        index = round(point_number * step)
        optimized_data.append(data[index])

    return optimized_data


def get_filter_query(date_from, date_to):
    query = "SELECT timestamp, lux FROM measurements WHERE timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC"

    try:
        from_str = parse_request_timestamp(date_from)
        to_str = parse_request_timestamp(date_to)
        print(f"Filtering from {from_str} to {to_str}")
        return query, [from_str, to_str]
    except Exception as error:
        print(f"Date parsing error: {error}")
        return query, [date_from, date_to]


def get_realtime_query(since_timestamp=None):
    query = "SELECT timestamp, lux FROM measurements ORDER BY timestamp ASC"

    if not since_timestamp:
        return query, []

    filtered_query = "SELECT timestamp, lux FROM measurements WHERE timestamp > ? ORDER BY timestamp ASC"

    try:
        since_str = parse_request_timestamp(since_timestamp)
        return filtered_query, [since_str]
    except Exception:
        return filtered_query, [since_timestamp]


def build_data_response(data, total_points):
    return {
        "data": data,
        "total_points": total_points,
        "rendered_points": len(data),
    }


def normalize_settings_payload(payload):
    settings = {
        "gain": float(payload.get("gain")),
        "it_ms": int(payload.get("it_ms")),
        "interval": float(payload.get("interval")),
        "samples": int(payload.get("samples")),
    }

    if settings["gain"] not in VALID_GAINS:
        raise ValueError("Gain musí byť jedna z hodnôt: 1, 2, 1/8 (0.125), 1/4 (0.25)")
    if settings["it_ms"] not in VALID_IT_TIMES:
        raise ValueError("Integration time musí byť jedna z hodnôt: 25, 50, 100, 200, 400, 800 ms")
    if not MIN_INTERVAL <= settings["interval"] <= MAX_INTERVAL:
        raise ValueError("Neplatný interval")
    if not MIN_SAMPLES <= settings["samples"] <= MAX_SAMPLES:
        raise ValueError("Neplatný počet vzoriek")

    return settings


def normalize_alert_payload(payload):
    enabled = bool(payload.get("enabled", False))
    email = str(payload.get("email", "")).strip()
    direction = str(payload.get("direction", "above")).strip()
    raw_threshold = payload.get("threshold")

    if direction not in VALID_ALERT_DIRECTIONS:
        raise ValueError("Neplatný režim upozornenia")

    if raw_threshold in (None, ""):
        if enabled:
            raise ValueError("Zadajte platnú hodnotu lux")
        threshold = 0.0
    else:
        try:
            threshold = float(raw_threshold)
        except (TypeError, ValueError):
            raise ValueError("Zadajte platnú hodnotu lux")

    if threshold < 0:
        raise ValueError("Hraničná hodnota lux musí byť nezáporná")

    if enabled and (not email or not EMAIL_REGEX.match(email)):
        raise ValueError("Zadajte platnú e-mailovú adresu")

    return {
        "enabled": enabled,
        "email": email,
        "threshold": threshold,
        "direction": direction,
    }


def load_emailjs_config():
    env_config = {
        "service_id": os.getenv("EMAILJS_SERVICE_ID", "").strip(),
        "template_id": os.getenv("EMAILJS_TEMPLATE_ID", "").strip(),
        "public_key": os.getenv("EMAILJS_PUBLIC_KEY", "").strip(),
        "private_key": os.getenv("EMAILJS_PRIVATE_KEY", "").strip(),
    }

    if all(env_config.values()):
        return env_config

    config = load_json_file(
        EMAILJS_CONFIG_FILE,
        {
            "service_id": "",
            "template_id": "",
            "public_key": "",
            "private_key": "",
        },
    )

    required_keys = ("service_id", "template_id", "public_key", "private_key")
    missing_keys = [key for key in required_keys if not str(config.get(key, "")).strip()]
    if missing_keys:
        raise RuntimeError(
            "EmailJS nie je nakonfigurovaný. Chýbajú: " + ", ".join(missing_keys)
        )

    return config


def build_emailjs_template_params(email, lux, threshold, direction, timestamp, test_email=False):
    direction_sk = "nad" if direction == "above" else "pod"
    subject_prefix = "Test upozornenia" if test_email else "Upozornenie na osvetlenie"

    return {
        "to_email": email,
        "subject": subject_prefix,
        "current_lux": f"{lux:.3f}",
        "threshold": f"{threshold:.3f}",
        "direction": direction_sk,
        "timestamp": timestamp,
        "message": (
            f"Systém zaznamenal hodnotu {lux:.3f} lx, ktorá je {direction_sk} "
            f"nastaveným limitom {threshold:.3f} lx. Čas udalosti: {timestamp}."
        ),
    }


def send_emailjs_email(template_params):
    config = load_emailjs_config()

    payload = {
        "service_id": config["service_id"],
        "template_id": config["template_id"],
        "user_id": config["public_key"],
        "accessToken": config["private_key"],
        "template_params": template_params,
    }

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "iot-light-monitor/1.0",
    }

    if APP_ORIGIN:
        headers["Origin"] = APP_ORIGIN
        headers["Referer"] = APP_ORIGIN + "/"

    response = requests.post(
        "https://api.emailjs.com/api/v1.0/email/send",
        json=payload,
        headers=headers,
        timeout=15,
    )

    print("EmailJS status:", response.status_code)
    print("EmailJS body:", response.text)

    if not response.ok:
        raise RuntimeError(f"EmailJS chyba {response.status_code}: {response.text}")

    return response.status_code, response.text


def evaluate_alert_condition(lux, settings):
    threshold = float(settings.get("threshold", 0))
    direction = settings.get("direction", "above")
    return lux > threshold if direction == "above" else lux < threshold


def trigger_alert_email(settings, lux, timestamp, test_email=False):
    template_params = build_emailjs_template_params(
        email=settings["email"],
        lux=float(lux),
        threshold=float(settings["threshold"]),
        direction=settings["direction"],
        timestamp=timestamp,
        test_email=test_email,
    )
    return send_emailjs_email(template_params)


def format_alert_timestamp(timestamp):
    try:
        return datetime.fromisoformat(timestamp).strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return timestamp


def handle_alert_for_measurement(lux, timestamp):
    settings = get_alert_settings()

    if not settings.get("enabled"):
        reset_alert_runtime_state()
        return

    current_condition = evaluate_alert_condition(lux, settings)

    with alert_runtime_lock:
        if current_condition:
            alert_runtime_state["consecutive_hits"] += 1
        else:
            alert_runtime_state["consecutive_hits"] = 0
            alert_runtime_state["last_condition"] = False
            return

        alert_condition_met = (
            alert_runtime_state["consecutive_hits"] >= ALERT_REQUIRED_CONSECUTIVE_HITS
        )
        previous_condition = alert_runtime_state["last_condition"]

        if alert_condition_met and not previous_condition:
            try:
                formatted_timestamp = format_alert_timestamp(timestamp)

                template_params = build_emailjs_template_params(
                    email=settings["email"],
                    lux=lux,
                    threshold=float(settings["threshold"]),
                    direction=settings["direction"],
                    timestamp=formatted_timestamp,
                    test_email=False,
                )

                status_code, status_text = send_emailjs_email(template_params)
                alert_runtime_state["last_sent_at"] = timestamp
                print(
                    f"Alert e-mail sent to {settings['email']} "
                    f"after {alert_runtime_state['consecutive_hits']} consecutive hits: "
                    f"{status_code} {status_text}"
                )
            except Exception as error:
                print(f"Email alert error: {error}")

        alert_runtime_state["last_condition"] = alert_condition_met


def publish_control_message(message):
    if not mqtt_client:
        return False, "MQTT client nie je dostupný"

    result = mqtt_client.publish(CONTROL_TOPIC, json.dumps(message))
    if result.rc != mqtt.MQTT_ERR_SUCCESS:
        return False, f"Nepodarilo sa odoslať MQTT správu (kód {result.rc})"

    return True, None


def get_app_config():
    return {
        "defaultRealtimePoints": DEFAULT_REALTIME_POINTS,
        "maxRealtimePoints": MAX_REALTIME_POINTS,
        "defaultFilterRenderLimit": DEFAULT_FILTER_RENDER_LIMIT,
        "maxFilterRenderLimit": MAX_FILTER_RENDER_LIMIT,
        "routes": {
            "data": "/data",
            "export": "/export",
            "clear": "/clear",
            "requestSettings": "/settings/request",
            "saveSettings": "/settings/save",
            "alertSettings": "/alerts/settings",
            "saveAlertSettings": "/alerts/settings",
            "testAlert": "/alerts/test",
        },
        "socketEvents": {
            "newData": "new_data_available",
            "clearGraph": "clear_graph",
            "settingsResponse": "settings_response",
            "settingsSaved": "settings_saved",
        },
    }


USERNAME, PASSWORD, BROKER, BROKER_PORT = load_credentials()
TOPIC = "esp32/sensor_data"
CONTROL_TOPIC = "esp32/control"

mqtt_client = None
last_processed_message = None
last_processed_time = 0


def on_connect(client, userdata, flags, rc):
    print("Connected to MQTT broker:", rc)
    client.subscribe(TOPIC)
    client.subscribe(CONTROL_TOPIC)
    print(f"Subscribed to {TOPIC} and {CONTROL_TOPIC}")


def on_message(client, userdata, msg):
    global last_processed_message, last_processed_time

    topic = msg.topic
    payload = msg.payload.decode()
    message_id = f"{topic}:{payload}"
    current_time = time.time()

    if message_id == last_processed_message and (current_time - last_processed_time) < 0.1:
        print(f"Skipping duplicate message: {payload}")
        return

    last_processed_message = message_id
    last_processed_time = current_time
    print(f"MQTT - Topic: {topic}, Payload: {payload}")

    if topic == TOPIC:
        try:
            data = json.loads(payload)
            lux = float(data.get("lux", 0))
            slovakia_now = get_slovakia_time()
            timestamp_with_tz = slovakia_now.isoformat()

            with get_db() as conn:
                last_record = conn.execute(
                    "SELECT lux, timestamp FROM measurements ORDER BY id DESC LIMIT 1"
                ).fetchone()

                if last_record:
                    last_time = datetime.fromisoformat(last_record["timestamp"])
                    time_diff = (slovakia_now - last_time).total_seconds()

                    if last_record["lux"] == lux and time_diff < 0.1:
                        print(f"Skipping duplicate - same lux {lux} within {time_diff:.2f}s")
                        return

                conn.execute(
                    "INSERT INTO measurements (timestamp, lux) VALUES (?, ?)",
                    (timestamp_with_tz, lux),
                )
                conn.commit()
                print(f"Inserted: lux={lux}, time={timestamp_with_tz}")

            handle_alert_for_measurement(lux, timestamp_with_tz)
            socketio.emit("new_data_available")

        except Exception as error:
            print("Error processing sensor data:", error)

    elif topic == CONTROL_TOPIC:
        try:
            data = json.loads(payload)

            if {"gain", "it_ms", "interval", "samples"}.issubset(data):
                print("Received settings from ESP32:", data)
                socketio.emit("settings_response", data)
            elif data.get("status") == "saved":
                print("Settings saved confirmation received")
                socketio.emit("settings_saved", data)

        except Exception as error:
            print("Error processing control message:", error)


def mqtt_thread():
    global mqtt_client

    mqtt_client = mqtt.Client()
    mqtt_client.username_pw_set(USERNAME, PASSWORD)
    mqtt_client.tls_set()
    mqtt_client.on_connect = on_connect
    mqtt_client.on_message = on_message

    mqtt_client_id = f"web_server_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    mqtt_client._client_id = mqtt_client_id.encode("utf-8")

    try:
        mqtt_client.connect(BROKER, BROKER_PORT, 60)
        print(f"MQTT client connected with ID: {mqtt_client_id}")
        mqtt_client.loop_forever()
    except Exception as error:
        print(f"MQTT connection error: {error}")
        time.sleep(5)
        mqtt_thread()


@app.route("/")
def index():
    return render_template("index.html", app_config=get_app_config())


@app.route("/app.js")
def app_js():
    return send_file(BASE_DIR / "app.js", mimetype="application/javascript")


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True}), 200


@app.route("/data")
def get_data():
    mode = request.args.get("mode", "realtime")
    date_from = request.args.get("from")
    date_to = request.args.get("to")
    max_points = request.args.get("max_points", type=int) or DEFAULT_REALTIME_POINTS
    since_timestamp = request.args.get("since")
    filter_render_limit = request.args.get("filter_render_limit", type=int) or DEFAULT_FILTER_RENDER_LIMIT

    if mode == "filter" and date_from and date_to:
        safe_render_limit = clamp(filter_render_limit, 1, MAX_FILTER_RENDER_LIMIT)
        query, params = get_filter_query(date_from, date_to)
        data = fetch_measurements(query, params)
        total_points = len(data)
        optimized_data = optimize_filter_data(data, safe_render_limit)
        return jsonify(build_data_response(optimized_data, total_points))

    query, params = get_realtime_query(since_timestamp)
    data = fetch_measurements(query, params)

    if not since_timestamp:
        safe_max_points = clamp(max_points, 1, MAX_REALTIME_POINTS)
        data = limit_realtime_data(data, safe_max_points)

    return jsonify(build_data_response(data, len(data)))


@app.route("/settings/request", methods=["POST"])
def request_settings():
    success, error_message = publish_control_message({"conf": True})
    status_code = 200 if success else 503
    return jsonify({"success": success, "message": error_message}), status_code


@app.route("/settings/save", methods=["POST"])
def save_settings():
    payload = request.get_json(silent=True) or {}

    try:
        settings = normalize_settings_payload(payload)
    except (TypeError, ValueError) as error:
        return jsonify({"success": False, "message": str(error)}), 400

    success, error_message = publish_control_message(settings)
    status_code = 200 if success else 503
    return jsonify({"success": success, "message": error_message}), status_code


@app.route("/alerts/settings", methods=["GET", "POST"])
def alert_settings():
    if request.method == "GET":
        settings = get_alert_settings()
        return jsonify(public_alert_settings(settings))

    payload = request.get_json(silent=True) or {}

    try:
        settings = normalize_alert_payload(payload)
        save_alert_settings(settings)
        reset_alert_runtime_state()
        return jsonify({
            "success": True,
            "message": "Nastavenie upozornenia bolo uložené",
            "settings": public_alert_settings(settings),
        })
    except ValueError as error:
        return jsonify({"success": False, "message": str(error)}), 400


@app.route("/alerts/test", methods=["POST"])
def test_alert():
    payload = request.get_json(silent=True) or {}

    try:
        settings = normalize_alert_payload(payload)
    except (TypeError, ValueError) as error:
        return jsonify({"success": False, "message": str(error)}), 400

    simulated_lux = (
        float(settings["threshold"]) + 1
        if settings["direction"] == "above"
        else max(0.0, float(settings["threshold"]) - 1)
    )

    now = get_slovakia_time().strftime("%d/%m/%Y %H:%M:%S")

    status_code, status_text = trigger_alert_email(
        settings=settings,
        lux=simulated_lux,
        timestamp=now,
        test_email=True,
    )

    return jsonify({
        "success": True,
        "message": "Testovací e-mail bol odoslaný",
        "emailjs_status": status_code,
        "emailjs_response": status_text,
    })


@app.route("/export")
def export_csv():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["timestamp (Slovakia timezone)", "lux"])

    with get_db() as conn:
        rows = conn.execute("SELECT timestamp, lux FROM measurements ORDER BY timestamp ASC").fetchall()
        for row in rows:
            writer.writerow([row["timestamp"], row["lux"]])

    csv_data = output.getvalue()
    output.close()
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=measurements.csv"},
    )


@app.route("/clear", methods=["POST"])
def clear_data():
    data = request.get_json(silent=True) or {}
    password = data.get("password", "")

    if not check_password_hash(CLEAR_PASSWORD_HASH, password):
        return jsonify({"message": "Nesprávne heslo"}), 403

    with get_db() as conn:
        conn.execute("DELETE FROM measurements")
        conn.commit()

    socketio.emit("clear_graph")
    return jsonify({"message": "Dáta boli vymazané"})


if __name__ == "__main__":
    init_db()
    init_alert_storage()

    thread = threading.Thread(target=mqtt_thread, daemon=True)
    thread.start()

    http_port = int(os.environ.get("PORT", 10000))
    socketio.run(app, host="0.0.0.0", port=http_port, debug=False, use_reloader=False)
