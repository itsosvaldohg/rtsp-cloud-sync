"""
ONVIF PullPoint Event Subscriber for rtsp-cloud-sync.
Subscribes to camera hardware motion notifications on TCP Port 2020 with auto-renewal and error backoff.
"""

import base64
import datetime
import hashlib
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from typing import Optional

from src.config import CameraConfig, SystemConfig
from src.motion_recorder import MotionRecorder


class OnvifEventListener:
    """
    Subscribes to ONVIF PullPoint event notifications on Tapo cameras (Port 2020).
    Dispatches motion detection events to MotionRecorder with auto-renewal and self-healing.
    """

    def __init__(self, camera: CameraConfig, system: SystemConfig, recorder: MotionRecorder):
        self.camera = camera
        self.system = system
        self.recorder = recorder
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._log_file = camera.get_log_file(system.log_dir)
        self._subscription_url: Optional[str] = None
        self._subscription_created_time: float = 0
        self._last_logged_error: str = ""
        self._error_log_count: int = 0

    def _log(self, message: str) -> None:
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_line = f"[{timestamp}] [{self.camera.name}] [ONVIF] {message}\n"
        try:
            with open(self._log_file, "a", encoding="utf-8") as f:
                f.write(log_line)
        except Exception:
            pass

    def _log_throttled_error(self, error_msg: str) -> None:
        """Logs error with throttling to prevent flooding log files."""
        if error_msg != self._last_logged_error:
            self._last_logged_error = error_msg
            self._error_log_count = 1
            self._log(f"Connection issue: {error_msg}")
        else:
            self._error_log_count += 1
            if self._error_log_count % 10 == 0:
                self._log(f"Connection issue: {error_msg} (Repeated {self._error_log_count} times)")

    def _build_wsse_header(self) -> str:
        """
        Builds standard WS-Security UsernameToken header with PasswordDigest.
        """
        nonce = secrets.token_bytes(16)
        nonce_b64 = base64.b64encode(nonce).decode("utf-8")
        created = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

        # Password_Digest = Base64( SHA-1( nonce + created + password ) )
        sha1_hasher = hashlib.sha1()
        sha1_hasher.update(nonce + created.encode("utf-8") + self.camera.password.encode("utf-8"))
        password_digest = base64.b64encode(sha1_hasher.digest()).decode("utf-8")

        return f"""
        <wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
                       xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
            <wsse:UsernameToken>
                <wsse:Username>{self.camera.username}</wsse:Username>
                <wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{password_digest}</wsse:Password>
                <wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{nonce_b64}</wsse:Nonce>
                <wsu:Created>{created}</wsu:Created>
            </wsse:UsernameToken>
        </wsse:Security>
        """

    def _create_pullpoint_subscription(self) -> Optional[str]:
        """
        Sends CreatePullPointSubscription SOAP request to camera ONVIF service.
        """
        url = f"http://{self.camera.ip}:{self.camera.onvif_port}/onvif/Events"
        header = self._build_wsse_header()
        soap_body = f"""<?xml version="1.0" encoding="utf-8"?>
        <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
                    xmlns:wsa="http://www.w3.org/2005/08/addressing"
                    xmlns:tev="http://www.onvif.org/ver10/events/wsdl">
            <s:Header>
                <wsa:Action>http://www.onvif.org/ver10/events/wsdl/EventPortType/CreatePullPointSubscriptionRequest</wsa:Action>
                <wsa:To>{url}</wsa:To>
                {header}
            </s:Header>
            <s:Body>
                <tev:CreatePullPointSubscription>
                    <tev:InitialTerminationTime>PT10M</tev:InitialTerminationTime>
                </tev:CreatePullPointSubscription>
            </s:Body>
        </s:Envelope>
        """

        req = urllib.request.Request(
            url,
            data=soap_body.encode("utf-8"),
            headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                xml_data = resp.read().decode("utf-8")
                root = ET.fromstring(xml_data)

                # Extract Subscription Reference Address
                for elem in root.iter():
                    if elem.tag.endswith("Address"):
                        sub_address = elem.text.strip()
                        if not sub_address.startswith("http"):
                            sub_address = f"http://{self.camera.ip}:{self.camera.onvif_port}{sub_address}"
                        self._subscription_created_time = time.time()
                        self._last_logged_error = ""
                        return sub_address
        except Exception as e:
            self._log_throttled_error(f"Failed to create PullPoint subscription on {url}: {e}")
            return None

        return None

    def _pull_messages(self, sub_url: str) -> bool:
        """
        Sends PullMessages request to subscription endpoint.
        Returns True if successful, False if subscription expired or failed.
        """
        header = self._build_wsse_header()
        soap_body = f"""<?xml version="1.0" encoding="utf-8"?>
        <s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
                    xmlns:wsa="http://www.w3.org/2005/08/addressing"
                    xmlns:tev="http://www.onvif.org/ver10/events/wsdl">
            <s:Header>
                <wsa:Action>http://www.onvif.org/ver10/events/wsdl/PullPointSubscription/PullMessagesRequest</wsa:Action>
                <wsa:To>{sub_url}</wsa:To>
                {header}
            </s:Header>
            <s:Body>
                <tev:PullMessages>
                    <tev:Timeout>PT5S</tev:Timeout>
                    <tev:MessageLimit>10</tev:MessageLimit>
                </tev:PullMessages>
            </s:Body>
        </s:Envelope>
        """

        req = urllib.request.Request(
            sub_url,
            data=soap_body.encode("utf-8"),
            headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                xml_data = resp.read().decode("utf-8")
                self._parse_notification(xml_data)
                self._last_logged_error = ""
                return True
        except urllib.error.HTTPError as e:
            # HTTP 500/400 indicates subscription is closed or expired
            self._log(f"PullPoint subscription expired (HTTP {e.code}). Re-subscribing...")
            return False
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # Network drop, timeout, or connection refused (sub endpoint closed)
            self._log_throttled_error(f"PullMessages endpoint error: {e}")
            return False
        except Exception as e:
            if not self._stop_event.is_set():
                self._log_throttled_error(f"Unexpected PullMessages error: {e}")
            return False

    def _parse_notification(self, xml_data: str) -> None:
        """
        Parses incoming ONVIF notification XML for motion state (namespace-agnostic).
        """
        if "NotificationMessage" not in xml_data:
            return

        try:
            root = ET.fromstring(xml_data)
            for elem in root.iter():
                local_tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag
                if local_tag == "NotificationMessage":
                    topic_text = ""
                    for child in elem.iter():
                        c_tag = child.tag.split("}")[-1] if "}" in child.tag else child.tag
                        if c_tag == "Topic":
                            topic_text = "".join(child.itertext()).strip()
                            break

                    # Check SimpleItem or Data elements
                    for item in elem.iter():
                        i_tag = item.tag.split("}")[-1] if "}" in item.tag else item.tag
                        if i_tag in ("SimpleItem", "ElementItem", "Data"):
                            name = item.attrib.get("Name", "")
                            value = item.attrib.get("Value", "")

                            is_active = value.lower() in ("true", "1", "active", "motion", "on", "start", "alarm", "high")
                            is_motion_topic = any(
                                k.lower() in topic_text.lower()
                                for k in ("motion", "cellmotion", "videoanalytics", "ruleengine", "detection", "detect", "tamper", "person", "human", "alarm", "event", "target")
                            )

                            if (is_motion_topic and is_active) or (name.lower() in ("ismotion", "state", "motion", "logicalstate", "alarm", "active") and is_active):
                                self.recorder.trigger_motion()
                                return

                    # Fallback match: if topic relates to motion/detection and payload indicates active state
                    if any(k.lower() in topic_text.lower() for k in ("motion", "cellmotion", "videoanalytics", "detection", "person", "alarm")):
                        lower_xml = xml_data.lower()
                        if any(v in lower_xml for v in ('value="true"', 'value="1"', 'value="on"', 'value="active"', '>true<', '>1<', '>on<')):
                            self.recorder.trigger_motion()
                            return

        except Exception as e:
            self._log(f"Error parsing notification XML: {e}")

    def _run_loop(self) -> None:
        """
        Main ONVIF subscription and polling loop with automatic 8-minute renewal.
        """
        self._log(f"Starting ONVIF Event Listener (Connecting to {self.camera.ip}:{self.camera.onvif_port})...")

        while not self._stop_event.is_set():
            sub_url = self._create_pullpoint_subscription()
            if not sub_url:
                self._stop_event.wait(timeout=10)
                continue

            self._log(f"ONVIF subscription active. Polling events on {sub_url}...")
            consecutive_failures = 0

            while not self._stop_event.is_set():
                # Proactively renew subscription every 8 minutes (before 10m expiry)
                age_seconds = time.time() - self._subscription_created_time
                if age_seconds >= 480:  # 8 minutes
                    self._log("Subscription reached 8m lifetime. Refreshing subscription proactively...")
                    break

                success = self._pull_messages(sub_url)
                if success:
                    consecutive_failures = 0
                    time.sleep(0.3)
                else:
                    consecutive_failures += 1
                    if consecutive_failures >= 2:
                        self._log("PullPoint connection failed 2 times consecutively. Recreating subscription...")
                        break
                    time.sleep(2)

            # Brief pause before next subscription creation cycle
            if not self._stop_event.is_set():
                self._stop_event.wait(timeout=3)

        self._log("ONVIF listener stopped cleanly.")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"ONVIF-{self.camera.name}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=10)
