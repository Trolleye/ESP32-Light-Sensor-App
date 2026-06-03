from machine import Pin, I2C

from veml7700 import VEML7700
from wifi import WiFi
from mqtt_client import MQTT_Client
from light_monitor_app import LightMonitorApp


def main():
    veml_i2c = I2C(0, sda=Pin(43), scl=Pin(44), freq=100000)
    sensor = VEML7700(veml_i2c)
    wifi = WiFi("wifi_credentials.json", ap_ssid="ESP32-Setup")
    mqtt_client = MQTT_Client()

    app = LightMonitorApp(sensor=sensor, mqtt_client=mqtt_client, wifi=wifi)
    app.start()


main()
