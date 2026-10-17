class HardwareError(Exception):
    """Base exception for hardware-related failures."""
    pass


class SerialConnectionError(HardwareError):
    """Raised when serial connection fails or drops."""
    pass


class SerialTimeoutError(HardwareError):
    """Raised when serial read/handshake times out."""
    pass


class SerialProtocolError(HardwareError):
    """Raised when serial device returns an error or malformed frame."""
    pass
