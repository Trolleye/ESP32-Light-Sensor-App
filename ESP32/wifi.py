import json
import socket
import time
import gc
import network
import esp



class WiFi:
    PORT = 80
    AP_IP = "192.168.4.1"

    def __init__(self, path, ap_ssid):
        self.config_path = path
        self.connected_ssid = None
        self.ssid_list = []

        self.release_port()
        wifi_configs = self.extract_wifi_credentials(path)

        self.hard_reset_network_interface()
        self.sta().active(True)

        print("Scanning for available networks...")
        available_networks = self.scan_networks()
        self.ssid_list = list(available_networks.keys())

        connected = False

        if not available_networks:
            print("No networks found during scan")

        for config in wifi_configs:
            ssid = config["ssid"]
            print(f"\nChecking if {ssid} is available...")

            if ssid in available_networks:
                print(f"{ssid} found in scan results")
                print(f"Attempting to connect to {ssid}...")
                if self.connect_to_wifi(ssid, config["password"]):
                    connected = True
                    print(self.get_connection_info())
                    break
            else:
                print(f"{ssid} not found in scan results")

        if not connected:
            print("Failed to connect to any WiFi network. Setting up AP mode")
            self.setup_access_point(ap_ssid)

    def sta(self):
        return network.WLAN(network.STA_IF)

    def ap(self):
        return network.WLAN(network.AP_IF)

    def release_port(self):
        sock = None
        try:
            gc.collect()
            sock = socket.socket()
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("0.0.0.0", self.PORT))
        except OSError:
            pass
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass

    def create_server_socket(self):
        addr = socket.getaddrinfo("0.0.0.0", self.PORT)[0][-1]
        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(addr)
        server.listen(1)
        return server

    def http_response(self, body, content_type="text/html", status="200 OK"):
        return (
            "HTTP/1.1 {status}\r\n"
            "Content-Type: {content_type}\r\n"
            "Connection: close\r\n"
            "\r\n"
            "{body}"
        ).format(status=status, content_type=content_type, body=body)

    def url_decode(self, value):
        value = value.replace("+", " ")
        pieces = value.split("%")
        if len(pieces) == 1:
            return value

        decoded = pieces[0]
        for piece in pieces[1:]:
            if len(piece) >= 2:
                try:
                    decoded += chr(int(piece[:2], 16)) + piece[2:]
                except ValueError:
                    decoded += "%" + piece
            else:
                decoded += "%" + piece
        return decoded

    def parse_form_data(self, body):
        params = {}
        if not body:
            return params

        for part in body.split("&"):
            if "=" in part:
                key, value = part.split("=", 1)
            else:
                key, value = part, ""
            params[self.url_decode(key)] = self.url_decode(value)
        return params

    def html_escape(self, value):
        return (
            str(value)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&#39;")
        )

    def build_setup_page(self):
        if self.ssid_list:
            options = "".join(
                f"<option value='{self.html_escape(ssid)}'>{self.html_escape(ssid)}</option>"
                for ssid in self.ssid_list
            )
            body = (
                "<h1>WiFi Setup</h1>"
                "<form method='POST'>"
                f"SSID: <select name='ssid'>{options}</select><br>"
                "Password: <input name='password' type='password'><br>"
                "<input type='submit'>"
                "</form>"
            )
        else:
            body = (
                "<h1>WiFi Setup</h1>"
                "<form method='POST'>"
                "SSID: <input name='ssid'><br>"
                "Password: <input name='password' type='password'><br>"
                "<input type='submit'>"
                "</form>"
            )

        return self.http_response(body)


    def reset_sta_for_connect(self, disable_ap=False):
        sta = self.sta()
        ap = self.ap()

        try:
            sta.disconnect()
        except Exception:
            pass

        sta.active(False)
        if disable_ap:
            ap.active(False)

        time.sleep(0.5)
        sta.active(True)
        time.sleep(0.5)
        return sta

    def setup_access_point(self, ap_ssid):
        ap = self.ap()
        ap.active(True)
        ap.config(essid=ap_ssid, password="", authmode=network.AUTH_OPEN)

        print(f"Access Point '{ap_ssid}' started at http://{self.AP_IP}")

        if not self.ssid_list:
            print("No networks found from previous scan")

        server = self.create_server_socket()

        try:
            while True:
                client = None
                connect_request = None
                try:
                    client, _ = server.accept()
                    request = client.recv(1024).decode()

                    if request.startswith("POST"):
                        body = request.partition("\r\n\r\n")[2]
                        form = self.parse_form_data(body)
                        ssid = form.get("ssid", "").strip()
                        pwd = form.get("password", "")

                        if not ssid:
                            response = self.http_response(
                                "SSID is required. <a href='/'>Try again</a>"
                            )
                            client.send(response.encode())
                            continue

                        response = self.http_response(
                            "Trying to connect. This access point may disappear while the device joins your WiFi.",
                            content_type="text/plain",
                        )
                        client.send(response.encode())
                        connect_request = (ssid, pwd)
                    else:
                        response = self.build_setup_page()
                        client.send(response.encode())
                except Exception as exc:
                    if client is not None:
                        error_response = self.http_response(
                            f"Request error: {self.html_escape(exc)}",
                            content_type="text/plain",
                            status="500 Internal Server Error",
                        )
                        try:
                            client.send(error_response.encode())
                        except Exception:
                            pass
                finally:
                    if client is not None:
                        try:
                            client.close()
                        except Exception:
                            pass

                if connect_request is not None:
                    try:
                        server.close()
                    except Exception:
                        pass

                    ssid, pwd = connect_request
                    if self.connect_to_wifi(ssid, pwd, disable_ap=True):
                        return True

                    print("Connection from setup AP failed; restarting setup AP")
                    ap.active(True)
                    ap.config(essid=ap_ssid, password="", authmode=network.AUTH_OPEN)
                    time.sleep(0.5)
                    server = self.create_server_socket()
        finally:
            try:
                server.close()
            except Exception:
                pass

            if self.sta().isconnected():
                ap.active(False)

    def scan_networks(self):
        wlan = self.sta()
        wlan.active(True)

        try:
            networks = wlan.scan()
        except Exception as exc:
            print(f"Scan failed: {exc}")
            return {}

        available = {}
        print(f"Found {len(networks)} network(s):")

        for net in networks:
            raw_ssid = net[0]
            ssid = raw_ssid.decode() if isinstance(raw_ssid, bytes) else str(raw_ssid)
            rssi = net[3]

            if ssid in available:
                available[ssid] = max(available[ssid], rssi)
            else:
                available[ssid] = rssi

            print(f"  - {ssid} (Signal: {rssi} dBm)")

        return available

    def extract_wifi_credentials(self, path):
        with open(path, "r") as f:
            data = json.load(f)

        if isinstance(data, dict):
            data = [data]
        elif not isinstance(data, list):
            raise ValueError("WiFi config must be a dict or list of dicts")

        configs = []
        for item in data:
            if not isinstance(item, dict):
                raise ValueError("Each WiFi config entry must be a dict")
            if "ssid" not in item or "password" not in item:
                raise ValueError("Each WiFi config entry must contain 'ssid' and 'password'")
            configs.append({"ssid": item["ssid"], "password": item["password"]})

        return configs

    def connect_to_wifi(self, ssid, password, timeout=15, disable_ap=False):
        wlan = self.reset_sta_for_connect(disable_ap=disable_ap)

        print(f"Connecting to {ssid}...")

        last_error = None
        for attempt in range(2):
            try:
                wlan.connect(ssid, password)
                break
            except OSError as exc:
                last_error = exc
                print(f"Connect call failed on attempt {attempt + 1}: {exc}")
                if attempt == 0:
                    wlan = self.reset_sta_for_connect(disable_ap=disable_ap)
                    continue
                print("Giving up after repeated connect() failure")
                return False

        start_time = time.time()
        while not wlan.isconnected():
            status = wlan.status()
            if status in (-3, -2, -1, 201, 202):
                print(f"\nConnection failed! Status: {self.get_status_meaning(status)}")
                return False
            if time.time() - start_time > timeout:
                if last_error is not None:
                    print(f"\nConnection failed! Last error: {last_error}")
                print(f"Connection failed! Status: {self.get_status_meaning(status)}")
                return False
            time.sleep(0.5)
            print(".", end="")

        self.connected_ssid = ssid
        print(f"\nSuccessfully connected! IP: {wlan.ifconfig()[0]}")
        return True

    def get_status_meaning(self, status):
        status_codes = {
            -3: "STAT_WRONG_PASSWORD",
            -2: "STAT_NO_AP_FOUND",
            -1: "STAT_CONNECT_FAIL",
            0: "STAT_IDLE",
            1: "STAT_CONNECTING",
            2: "STAT_WRONG_PASSWORD",
            3: "STAT_GOT_IP",
            1000: "STAT_IDLE",
            1001: "STAT_CONNECTING",
            202: "STAT_WRONG_PASSWORD",
            201: "STAT_NO_AP_FOUND",
            1010: "STAT_GOT_IP",
        }
        return status_codes.get(status, f"Unknown status: {status}")

    def get_connection_info(self):
        wlan = self.sta()
        if wlan.isconnected():
            return {"ip": wlan.ifconfig()[0], "ssid": self.connected_ssid}
        return None

    def hard_reset_network_interface(self):
        sta = self.sta()
        ap = self.ap()

        sta.active(False)
        ap.active(False)

        time.sleep(1)

        if esp is not None:
            try:
                esp.osdebug(None)
            except Exception:
                pass

        sta.active(True)
        time.sleep(0.5)

    def disconnect(self):
        wlan = self.sta()
        if wlan.isconnected():
            wlan.disconnect()
        wlan.active(False)
        self.connected_ssid = None
        print("Disconnected from WiFi")
