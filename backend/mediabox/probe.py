"""The health check of the media box: `python -m mediabox.probe`.

Asks the box for its status over its socket and ends with 0 when it answers `ok`.
"""

import json
import os
import socket
import sys
from typing import Final

TIMEOUT_SECONDS: Final = 2.0


def main() -> int:
    path = os.environ.get("BROADCAST_MEDIA_SOCKET", "")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(TIMEOUT_SECONDS)
            connection.connect(path)
            connection.sendall(b'{"op":"status"}\n')
            answer = connection.makefile("rb").readline(4096)
        return 0 if json.loads(answer).get("ok") is True else 1
    except OSError, ValueError, AttributeError:
        return 1


if __name__ == "__main__":
    sys.exit(main())
