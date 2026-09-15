from __future__ import annotations

import socket
import subprocess

from zeroconf import IPVersion, ServiceInfo, Zeroconf


def local_ipv4() -> str:
    for interface in ("en0", "en1", "en2", "en3"):
        result = subprocess.run(
            ["ipconfig", "getifaddr", interface], check=False, capture_output=True, text=True
        )
        candidate = result.stdout.strip()
        if candidate.startswith(("10.", "192.168.")) or candidate.startswith(tuple(f"172.{item}." for item in range(16, 32))):
            return candidate
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("1.1.1.1", 53))
        return probe.getsockname()[0]
    finally:
        probe.close()


class MdnsPublisher:
    def __init__(self, port: int):
        address = socket.inet_aton(local_ipv4())
        self.zeroconf = Zeroconf(ip_version=IPVersion.V4Only)
        self.info = ServiceInfo(
            "_cardputer-sync._tcp.local.",
            "cardputer-sync._cardputer-sync._tcp.local.",
            addresses=[address],
            port=port,
            properties={"api": "v1"},
            server="cardputer-sync.local.",
        )

    def start(self) -> None:
        self.zeroconf.register_service(self.info)

    def close(self) -> None:
        self.zeroconf.unregister_service(self.info)
        self.zeroconf.close()
