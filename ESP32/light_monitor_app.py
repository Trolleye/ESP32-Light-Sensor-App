import json
import time


class LightMonitorApp:
    CONFIG_FILE = "measurement_config.json"
    CONFIG_TOPIC = "esp32/control"

    VALID_GAINS = (2, 1, 0.25, 0.125)
    VALID_ITS = (25, 50, 100, 200, 400, 800)

    DEFAULT_GAIN = 1
    DEFAULT_IT_MS = 100
    DEFAULT_INTERVAL = 5.0
    DEFAULT_SAMPLES = 3

    def __init__(self, sensor, mqtt_client, wifi=None, config_file=None, config_topic=None):
        self.sensor = sensor
        self.mqtt_client = mqtt_client
        self.wifi = wifi

        self.config_file = config_file or self.CONFIG_FILE
        self.config_topic = config_topic or self.CONFIG_TOPIC

        self.current_gain = self.DEFAULT_GAIN
        self.current_it_ms = self.DEFAULT_IT_MS
        self.publish_interval = self.DEFAULT_INTERVAL
        self.sample_count = self.DEFAULT_SAMPLES

        self._last_publish_ms = None

        self.load_and_apply_config()

    def decode_message(self, msg):
        if isinstance(msg, bytes):
            return msg.decode()
        return msg

    def validate_gain(self, gain):
        try:
            gain = float(gain)
        except (TypeError, ValueError):
            return None
        return gain if gain in self.VALID_GAINS else None

    def validate_it(self, it_ms):
        try:
            it_ms = int(it_ms)
        except (TypeError, ValueError):
            return None
        return it_ms if it_ms in self.VALID_ITS else None

    def validate_interval(self, interval):
        try:
            interval = float(interval)
        except (TypeError, ValueError):
            return None
        return interval if interval > 0 else None

    def validate_samples(self, samples):
        try:
            samples = int(samples)
        except (TypeError, ValueError):
            return None
        return samples if samples > 0 else None

    def apply_sensor_config(self, gain, it_ms):
        self.sensor.set_config(gain=gain, it_ms=it_ms)
        self.current_gain = gain
        self.current_it_ms = it_ms

    def load_and_apply_config(self):
        gain = self.DEFAULT_GAIN
        it_ms = self.DEFAULT_IT_MS
        interval = self.DEFAULT_INTERVAL
        samples = self.DEFAULT_SAMPLES

        try:
            with open(self.config_file, "r") as handle:
                raw = handle.read().strip()

            if raw:
                stored = json.loads(raw)

                stored_gain = self.validate_gain(stored.get("gain", gain))
                stored_it = self.validate_it(stored.get("it", stored.get("it_ms", it_ms)))
                stored_interval = self.validate_interval(stored.get("interval", interval))
                stored_samples = self.validate_samples(stored.get("samples", samples))

                if stored_gain is not None:
                    gain = stored_gain
                if stored_it is not None:
                    it_ms = stored_it
                if stored_interval is not None:
                    interval = stored_interval
                if stored_samples is not None:
                    samples = stored_samples

        except (OSError, ValueError, TypeError):
            pass

        try:
            self.apply_sensor_config(gain, it_ms)
        except Exception:
            self.apply_sensor_config(self.DEFAULT_GAIN, self.DEFAULT_IT_MS)
            interval = self.DEFAULT_INTERVAL
            samples = self.DEFAULT_SAMPLES

        self.publish_interval = interval
        self.sample_count = samples

    def save_config(self):
        payload = {
            "gain": self.current_gain,
            "it": self.current_it_ms,
            "interval": self.publish_interval,
            "samples": self.sample_count
        }

        try:
            with open(self.config_file, "w") as handle:
                handle.write(json.dumps(payload))
        except OSError as exc:
            print("Nepodarilo sa ulozit konfiguraciu:", exc)

    def publish_raw(self, topic, payload):
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload)

        try:
            if hasattr(self.mqtt_client, "publish"):
                self.mqtt_client.publish(topic, payload)
                return

            raw_client = getattr(self.mqtt_client, "mqtt_client", None)
            if raw_client is None:
                raw_client = getattr(self.mqtt_client, "client", None)

            if raw_client is None:
                raise AttributeError("MQTT publish API not found")

            raw_client.publish(topic, payload)
        except Exception:
            self.mqtt_client.connect()

            if hasattr(self.mqtt_client, "publish"):
                self.mqtt_client.publish(topic, payload)
                return

            raw_client = getattr(self.mqtt_client, "mqtt_client", None)
            if raw_client is None:
                raw_client = getattr(self.mqtt_client, "client", None)

            if raw_client is None:
                raise AttributeError("MQTT publish API not found after reconnect")

            raw_client.publish(topic, payload)

    def publish_current_config(self):
        payload = {
            "gain": self.current_gain,
            "it_ms": self.current_it_ms,
            "interval": self.publish_interval,
            "samples": self.sample_count
        }
        self.publish_raw(self.config_topic, payload)

    def handle_config_update(self, data):
        new_gain = self.validate_gain(data.get("gain", self.current_gain))
        new_it_ms = self.validate_it(data.get("it_ms", self.current_it_ms))
        new_interval = self.validate_interval(data.get("interval", self.publish_interval))
        new_samples = self.validate_samples(data.get("samples", self.sample_count))

        if new_gain is None:
            new_gain = self.current_gain
        if new_it_ms is None:
            new_it_ms = self.current_it_ms
        if new_interval is None:
            new_interval = self.publish_interval
        if new_samples is None:
            new_samples = self.sample_count

        changed = (
            new_gain != self.current_gain or
            new_it_ms != self.current_it_ms or
            new_interval != self.publish_interval or
            new_samples != self.sample_count
        )

        if not changed:
            return

        self.apply_sensor_config(new_gain, new_it_ms)
        self.publish_interval = new_interval
        self.sample_count = new_samples
        self.save_config()

        try:
            self.publish_raw(self.config_topic, {"status": "saved"})
        except Exception:
            pass

    def mqtt_callback(self, topic, msg):
        try:
            data = json.loads(self.decode_message(msg))
            print("Received:", data)

            if not isinstance(data, dict):
                return

            if data.get("status") == "saved":
                return

            if data.get("conf") is True:
                self.publish_current_config()
                return

            config_keys = ("gain", "it_ms", "interval", "samples")
            if not any(key in data for key in config_keys):
                return

            self.handle_config_update(data)

        except Exception as exc:
            print("Callback error:", exc)

    def measure_lux(self):
        if self.sample_count > 1:
            total_lux = 0.0
            for index in range(self.sample_count):
                total_lux += self.sensor.read_lux()
            lux = total_lux / self.sample_count
        else:
            lux = self.sensor.read_lux()

        return round(lux, 3)

    def should_publish(self):
        now_ms = time.ticks_ms()

        if self._last_publish_ms is None:
            self._last_publish_ms = now_ms
            return True

        interval_ms = int(self.publish_interval * 1000)
        if time.ticks_diff(now_ms, self._last_publish_ms) >= interval_ms:
            self._last_publish_ms = now_ms
            return True

        return False

    def start(self):
        self.mqtt_client.set_callback(self.mqtt_callback)
        self.mqtt_client.connect()

        while True:
            self.mqtt_client.check_messages()

            if self.should_publish():
                lux = self.measure_lux()
                self.mqtt_client.publish_data(lux)