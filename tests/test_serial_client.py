import concurrent.futures
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
import serial

from src.core.exceptions import (
    SerialConnectionError,
    SerialProtocolError,
    SerialTimeoutError,
)
from src.hardware.serial_client import SerialClient


@pytest.fixture
def mock_serial():
    with patch("src.hardware.serial_client.serial.Serial") as mock_cls:
        instance = MagicMock()
        instance.is_open = True
        mock_cls.return_value = instance
        yield instance


def test_handshake_success(mock_serial):
    """Asserts client succeeds when READY\\n is returned."""
    mock_serial.readline.return_value = b"READY\n"
    client = SerialClient(port="COM3", handshake_timeout=1.0)
    assert client.port == "COM3"
    assert client._serial is not None
    client.close()


def test_handshake_timeout(mock_serial):
    """Asserts proper exception is raised if READY\\n never arrives."""
    mock_serial.readline.return_value = b""
    with pytest.raises(SerialTimeoutError) as exc_info:
        SerialClient(port="COM3", handshake_timeout=0.05)
    assert "READY" in str(exc_info.value)


def test_ping_pong(mock_serial):
    """Verifies PING\\n sends correctly and parses PONG\\n."""
    mock_serial.readline.side_effect = [b"READY\n", b"PONG\n"]
    client = SerialClient(port="COM3")
    assert client.ping() is True
    mock_serial.write.assert_called_with(b"PING\n")
    client.close()


def test_set_laser(mock_serial):
    """Verifies LASER:1\\n -> OK:LASER=1 and LASER:0\\n -> OK:LASER=0."""
    mock_serial.readline.side_effect = [
        b"READY\n",
        b"OK:LASER=1\n",
        b"OK:LASER=0\n",
    ]
    client = SerialClient(port="COM3")

    state_on = client.set_laser(True)
    assert state_on is True
    mock_serial.write.assert_called_with(b"LASER:1\n")

    state_off = client.set_laser(False)
    assert state_off is False
    mock_serial.write.assert_called_with(b"LASER:0\n")
    client.close()


def test_set_position(mock_serial):
    """Verifies POS:90,30\\n -> returns (90, 30)."""
    mock_serial.readline.side_effect = [b"READY\n", b"OK:POS=90,30\n"]
    client = SerialClient(port="COM3")
    pan, tilt = client.set_position(90, 30)
    assert pan == 90
    assert tilt == 30
    mock_serial.write.assert_called_with(b"POS:90,30\n")
    client.close()


def test_step(mock_serial):
    """Verifies STEP:2,-1\\n -> returns updated coordinates."""
    mock_serial.readline.side_effect = [b"READY\n", b"OK:STEP=92,29\n"]
    client = SerialClient(port="COM3")
    pan, tilt = client.step(2, -1)
    assert pan == 92
    assert tilt == 29
    mock_serial.write.assert_called_with(b"STEP:2,-1\n")
    client.close()


def test_get_limits(mock_serial):
    """Verifies LIMITS?\\n -> returns (10, 170, 0, 45)."""
    mock_serial.readline.side_effect = [b"READY\n", b"OK:LIMITS=10,170,0,45\n"]
    client = SerialClient(port="COM3")
    limits = client.get_limits()
    assert limits == (10, 170, 0, 45)
    mock_serial.write.assert_called_with(b"LIMITS?\n")
    client.close()


def test_device_error_response(mock_serial):
    """Verifies ERR:UNKNOWN_CMD raises SerialProtocolError."""
    mock_serial.readline.side_effect = [b"READY\n", b"ERR:UNKNOWN_CMD\n"]
    client = SerialClient(port="COM3")
    with pytest.raises(SerialProtocolError) as exc_info:
        client.ping()
    assert "ERR:UNKNOWN_CMD" in str(exc_info.value)
    client.close()


def test_command_timeout(mock_serial):
    """Verifies empty readline on command raises SerialTimeoutError."""
    mock_serial.readline.side_effect = [b"READY\n", b""]
    client = SerialClient(port="COM3")
    with pytest.raises(SerialTimeoutError):
        client.ping()
    client.close()


def test_thread_safety(mock_serial):
    """Spawns 10 concurrent threads issuing commands to ensure the Lock prevents interleaved frames."""
    in_flight = False
    violations = 0
    current_cmd = ""
    lock = threading.Lock()

    def mock_write(data):
        nonlocal in_flight, violations, current_cmd
        with lock:
            if in_flight:
                violations += 1
            in_flight = True
            current_cmd = data.decode("ascii").strip()

    def mock_readline():
        nonlocal in_flight, current_cmd
        time.sleep(0.002)  # Simulate small hardware communication delay
        with lock:
            in_flight = False
            if current_cmd.startswith("PING"):
                return b"PONG\n"
            elif current_cmd.startswith("POS:"):
                coords = current_cmd.split(":")[1]
                return f"OK:POS={coords}\n".encode("ascii")
            elif current_cmd.startswith("STEP:"):
                return b"OK:STEP=90,20\n"
            elif current_cmd.startswith("LASER:"):
                return b"OK:LASER=1\n"
            elif current_cmd == "LIMITS?":
                return b"OK:LIMITS=10,170,0,45\n"
            return b"OK\n"

    # Handshake first
    mock_serial.readline.return_value = b"READY\n"
    client = SerialClient(port="COM3")

    mock_serial.write.side_effect = mock_write
    mock_serial.readline.side_effect = mock_readline

    def worker(thread_idx: int):
        for i in range(15):
            res_ping = client.ping()
            assert res_ping is True
            pos = client.set_position(90 + (thread_idx % 10), 20)
            assert pos == (90 + (thread_idx % 10), 20)
            step_res = client.step(1, 0)
            assert step_res == (90, 20)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    client.close()
    assert violations == 0, f"Thread safety violated! Found {violations} interleaved transactions."
