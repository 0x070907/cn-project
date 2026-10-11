"""Focused tests for the receiver's fairness recording.

These drive a real receiver process over UDP (localhost), but redirect its CSV
output to a temporary directory so existing metrics/fairness results are never
touched.
"""

from __future__ import annotations

import csv
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "app"
sys.path.insert(0, str(APP_DIR))

import protocol  # noqa: E402


def _ports_for(base: int) -> dict[int, int]:
    return {flow_id: base + flow_id - 1 for flow_id in protocol.flow_ids.values()}


def _count_rows(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with path.open(newline="") as file:
            return max(0, sum(1 for _ in csv.reader(file)) - 1)
    except (OSError, csv.Error):
        return 0


def _start_run(
    ports: dict[int, int], run_id: int, flow_id: int, count: int
) -> socket.socket:
    """Send START until the receiver acks; return the socket to keep using."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(0.3)
    message = protocol.pack_control(protocol.start_message, run_id, flow_id, count)

    deadline = time.time() + 5.0
    while time.time() < deadline:
        sock.sendto(message, ("127.0.0.1", ports[flow_id]))
        try:
            response, _ = sock.recvfrom(1024)
        except socket.timeout:
            continue

        control = protocol.unpack_control(response)
        if (
            control is not None
            and control[0] == protocol.start_ack
            and control[1] == run_id
            and control[2] == flow_id
        ):
            return sock

    sock.close()
    raise AssertionError(f"receiver never acked START for flow {flow_id}")


def _send_data(
    sock: socket.socket,
    ports: dict[int, int],
    run_id: int,
    flow_id: int,
    count: int,
) -> None:
    for sequence in range(count):
        packet = protocol.pack_data(
            flow_id, sequence, run_id, time.monotonic_ns(), b"x" * 100
        )
        sock.sendto(packet, ("127.0.0.1", ports[flow_id]))
        time.sleep(0.01)


def _end_run(
    sock: socket.socket,
    ports: dict[int, int],
    run_id: int,
    flow_id: int,
    count: int,
) -> None:
    message = protocol.pack_control(protocol.end_message, run_id, flow_id, count)
    for _ in range(3):
        sock.sendto(message, ("127.0.0.1", ports[flow_id]))
        time.sleep(0.05)


def _run_flow(
    ports: dict[int, int], run_id: int, flow_id: int, count: int
) -> None:
    sock = _start_run(ports, run_id, flow_id, count)
    try:
        _send_data(sock, ports, run_id, flow_id, count)
        _end_run(sock, ports, run_id, flow_id, count)
    finally:
        sock.close()


class ReceiverFairnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)

        self.csv_path = Path(self.tmpdir.name) / "metrics.csv"
        self.fairness_path = self.csv_path.with_name("fairness.csv")

        # keep the ports unique-ish without relying on the real 5001-5004 range
        base = 5500 + (os.getpid() % 40) * 4
        self.ports = _ports_for(base)

        self.proc = self._start_receiver()
        self.addCleanup(self._stop_receiver)

    def _start_receiver(self) -> subprocess.Popen[bytes]:
        code = (
            "import sys, pathlib\n"
            f"sys.path.insert(0, {str(APP_DIR)!r})\n"
            "import metrics, protocol, receiver\n"
            f"metrics.csv_path = pathlib.Path({str(self.csv_path)!r})\n"
            f"protocol.flow_ports = {self.ports!r}\n"
            "receiver.main()\n"
        )
        return subprocess.Popen(
            [sys.executable, "-c", code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _stop_receiver(self) -> None:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGINT)
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    def _wait_for_fairness_rows(self, expected: int, timeout: float = 6.0) -> int:
        deadline = time.time() + timeout
        rows = _count_rows(self.fairness_path)
        while rows < expected and time.time() < deadline:
            time.sleep(0.05)
            rows = _count_rows(self.fairness_path)
        return rows

    def test_two_flow_run_records_exactly_one_fairness_row(self) -> None:
        run_id = 101
        count = 3

        _run_flow(self.ports, run_id, protocol.flow_ids["high"], count)
        _run_flow(self.ports, run_id, protocol.flow_ids["medium"], count)

        rows = self._wait_for_fairness_rows(1)
        self.assertEqual(rows, 1, "a two-flow run must record exactly one fairness row")

        with self.fairness_path.open(newline="") as file:
            row = next(csv.DictReader(file))

        self.assertGreater(float(row["high_mbps"]), 0)
        self.assertGreater(float(row["medium_mbps"]), 0)

    def test_four_flow_run_records_exactly_one_fairness_row(self) -> None:
        run_id = 202
        count = 3

        for name in ("high", "medium", "low", "background"):
            _run_flow(self.ports, run_id, protocol.flow_ids[name], count)

        rows = self._wait_for_fairness_rows(1)
        self.assertEqual(rows, 1, "a four-flow run must record exactly one fairness row")

    def test_fairness_waits_for_all_announced_flows(self) -> None:
        run_id = 303
        count = 3
        high_id = protocol.flow_ids["high"]
        medium_id = protocol.flow_ids["medium"]

        high_sock = _start_run(self.ports, run_id, high_id, count)
        medium_sock = _start_run(self.ports, run_id, medium_id, count)
        try:
            # only high completes; medium has announced but not finished yet
            _send_data(high_sock, self.ports, run_id, high_id, count)
            _end_run(high_sock, self.ports, run_id, high_id, count)

            # wait comfortably past the receiver's 1s grace period
            time.sleep(2.0)
            self.assertEqual(
                _count_rows(self.fairness_path),
                0,
                "fairness must not be recorded before medium completes",
            )

            # now the announced medium flow completes
            _send_data(medium_sock, self.ports, run_id, medium_id, count)
            _end_run(medium_sock, self.ports, run_id, medium_id, count)
        finally:
            high_sock.close()
            medium_sock.close()

        rows = self._wait_for_fairness_rows(1)
        self.assertEqual(rows, 1, "fairness is recorded once all announced flows finish")


if __name__ == "__main__":
    unittest.main()
