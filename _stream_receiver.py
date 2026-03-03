"""
AnnounceFlow - Placeholder Stream Receiver (V1)

Listens on a UDP port. Actual audio decode is out of Phase 3 scope.
This script is spawned by StreamManager as a subprocess.

Usage: python _stream_receiver.py [port]
"""
import signal
import socket
import sys


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5800
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", port))
    sock.settimeout(1.0)

    running = True

    def _handle_signal(signum, frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    while running:
        try:
            sock.recvfrom(4096)
        except socket.timeout:
            continue
        except OSError:
            break

    sock.close()


if __name__ == "__main__":
    main()
