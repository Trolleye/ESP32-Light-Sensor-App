import time
from umqtt.simple import MQTTClient
import ubinascii
import machine
import json
import ssl

PUBLISH_TOPIC = "esp32/sensor_data"
CONTROL_TOPIC = "esp32/control"

class MQTT_Client:
    def __init__(self, publish_topic=PUBLISH_TOPIC, control_topic=CONTROL_TOPIC, path="mqtt_credentials.json"):
        
        self.publish_topic = publish_topic
        self.control_topic = control_topic
        self.client_id = ubinascii.hexlify(machine.unique_id()).decode()
        self.mqtt_client = None
        self.connected = False
        self.callback = None
        
        
        with open(path, "r") as f:
            data = json.load(f)
        self.username = data.get("username")
        self.password = data.get("password")
        self.broker_ip = data.get("broker_ip")
        self.broker_port = data.get("broker_port")
    
    def set_callback(self, callback):
        self.callback = callback
    
    def connect(self):
        try:
            ssl_params = {
                "server_hostname": self.broker_ip,
            }
            
            self.mqtt_client = MQTTClient(
                client_id=self.client_id,
                server=self.broker_ip,
                port=self.broker_port,
                user=self.username,
                password=self.password,
                keepalive=60,
                ssl=True,
                ssl_params=ssl_params
            )
            
            print(f"Connecting to {self.broker_ip}:{self.broker_port}...")
            print(f"Client ID: {self.client_id}")
            print(f"Username: {self.username}")
            
            self.mqtt_client.connect()
            self.connected = True
            print(f"Connected to MQTT broker at {self.broker_ip}")
            
            if self.callback:
                self.mqtt_client.set_callback(self.callback)
                self.mqtt_client.subscribe(self.control_topic)
                print(f"Subscribed to {self.control_topic}")
                        
            return True
            
        except Exception as e:
            print(f"MQTT connection failed: {e}")
            import sys
            sys.print_exception(e)
            self.connected = False
            return False
        
    def check_messages(self):
        if self.connected:
            try:
                self.mqtt_client.check_msg()
            except Exception as e:
                print(f"Message check failed: {e}")
                self.connected = False
                
    def publish_data(self, data):
        if not self.connected:
            print("Not connected to broker. Attempting to connect...")
            if not self.connect():
                print("Failed to connect. Cannot publish data.")
                return False
        
        message = {
            "lux": data,
        }
        
        message_str = json.dumps(message)
        try:
            self.mqtt_client.publish(self.publish_topic, message_str)
            print(f"Published: {message_str}")
            return True
        except Exception as e:
            print(f"Publish failed: {e}")
            self.connected = False
            
            try:
                self.mqtt_client.connect()
                self.connected = True
                print("Reconnected to broker")
                self.mqtt_client.publish(self.publish_topic, message_str)
                print(f"Published after reconnection: {message_str}")
                return True
            except Exception as reconnect_error:
                print(f"Reconnection failed: {reconnect_error}")
                return False
    
    def disconnect(self):
        if self.mqtt_client and self.connected:
            try:
                self.mqtt_client.disconnect()
                self.connected = False
                print("Disconnected from MQTT broker")
            except Exception as e:
                print(f"Disconnect failed: {e}")