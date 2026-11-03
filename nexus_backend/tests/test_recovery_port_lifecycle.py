"""Native TCP evidence: a closed test server is not an occupied listener."""

import socket
import unittest
from unittest.mock import patch

from scripts import p01_local_recovery_env as environment


class RecoveryPortLifecycleTests(unittest.TestCase):
    def mapped_probe(self, port):
        original = socket.socket

        class Probe(original):
            def bind(self, address):
                return super().bind((address[0], port))

        return patch.object(environment.socket, "socket", Probe)

    def test_live_listener_is_never_available(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            with self.mapped_probe(listener.getsockname()[1]):
                self.assertEqual(len(environment.occupied_ports()), 7)

    def test_closed_listener_time_wait_does_not_block_next_identity_or_shutdown(self):
        with socket.socket() as listener, socket.socket() as client:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            listener.listen()
            client.settimeout(2)
            client.connect(("127.0.0.1", port))
            connection, _ = listener.accept()
            with connection:
                connection.shutdown(socket.SHUT_RDWR)
            self.assertEqual(client.recv(1), b"")
        with self.mapped_probe(port):
            self.assertEqual(environment.occupied_ports(), [])


if __name__ == "__main__":
    unittest.main()
