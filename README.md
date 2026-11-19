# LogServer

Cross-platform Python/PySide6 desktop log receiver for IoT and other devices. Receives TCP and UDP messages, displays colored text, and optionally writes every received severity to combined or per-client files. Uses no database or ICMP probing.

## Run

Requires Python 3.11 or newer and PySide6 6.12.0. The existing virtual environment can run the application on this Linux checkout:

```bash
.venv/bin/python log_server.py
```

For a new environment, install [requirements.txt](requirements.txt) using `python -m pip install -r requirements.txt`, then run `python log_server.py`. On Windows, use the Python executable in your environment, for example `.venv\Scripts\python.exe log_server.py`. Native Windows and macOS execution has not yet been verified.

First launch opens setup before the main window. Choose the received-log folder and use **Socket and other preferences** to configure the listener. Configuration and state locations are shown in setup and are chosen through Qt's operating-system standard paths. Subsequent launches reuse saved preferences and window geometry. `python log_server.py --install` reopens setup without deleting logs.

The default listener is TCP on `127.0.0.1:5514`. To receive IPv4 devices on your network, set the bind address to `0.0.0.0` in Preferences and permit the selected port through your firewall. For IPv6, use an explicit address such as `::1` or `::`; IPv6 listeners accept IPv6 clients. Select TCP, UDP, or both on the configured port. Start/Stop controls the listener; socket and file changes apply at the next Start. Appearance, fonts, display limit, and keywords apply immediately.

This receiver uses plain sockets without authentication or encryption. Use it on trusted networks; keep it behind a firewall or a protected tunnel for remote devices. UDP delivery is best effort and has no acknowledgement.

## Linux desktop launcher

The Linux desktop launcher has been tested and works on the current Linux system: 
- Linux Kernel Version: 7.2.15-arch1-1 (64-bit)
- KDE Plasma Version: 6.8.2
- KDE Frameworks Version: 6.31.0
It opens LogServer from the Applications menu using the app icon, without a terminal window.

The [launcher file](logserver.desktop) points to this checkout at `/home/peterg/Development/Python/LogServer` and its `.venv/bin/python` interpreter. Before installing on another machine or from another folder, edit its `Exec` and `Icon` paths to match your checkout and Python environment. The environment must have the required dependencies installed.

Run these commands from the project folder to install the launcher for your user:

```bash
mkdir -p "${XDG_DATA_HOME:-$HOME/.local/share}/applications"
install -m 644 logserver.desktop "${XDG_DATA_HOME:-$HOME/.local/share}/applications/logserver.desktop"
```

Open the Applications menu and search for **LogServer**. If you move the project or its Python environment, update the paths in `logserver.desktop` and repeat the installation commands.

## Device messages

TCP messages are newline-delimited bytes in the selected encoding, UTF-8 by default. A final unterminated line is received when the client disconnects or the idle timeout expires. UDP uses one datagram per message. Python's pickled `logging.handlers.SocketHandler` format and octet-counted syslog framing are not supported.

Accepted message content:

- Plain text, including common severity words such as `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`.
- Syslog text beginning with a priority such as `<11>`; severity is extracted from the priority.
- JSON objects with `message` or `msg`, and optional `level` or `severity`. Additional fields remain visible and are saved.

Examples of message content:

```text
INFO ^sensor-1^ temperature=24.5 C
ERROR ^pump-2^ motor failed
<11>Oct 28 10:00:00 pico sensor read failed
{"level":"warning","message":"temperature high","device":"pico","temperature":42.7}
```

A complete TCP sender using Python's standard library:

```python
import socket

with socket.create_connection(("127.0.0.1", 5514), timeout=5) as client:
    client.sendall(b"INFO ^sensor-1^ temperature=24.5 C\n")
```

The display colors severity, comma-separated literal keywords configured in Preferences, and sections surrounded by `^`. Text and markers stay selectable. Incoming HTML is displayed literally; terminal escape sequences and control characters are removed. Invalid encoded bytes are replaced. Display history is bounded independently of files, so clearing the display does not remove saved logs.

## Received log files

Preferences selects **One combined file** or **One file per client IP**, the folder, combined file name, and rotation size/backup count. Existing files are appended. Rotation removes the oldest retained backup when the configured count is exceeded. Disabling rotation allows files to grow without a size limit.

Per-client file names use a stable hash of the socket IP address; every record includes the original IP, reception time with timezone, transport, and severity. Connections from the same IP share a file, including devices behind the same NAT. A device cannot supply a file path. File logging applies to every severity regardless of the application diagnostic level.

`application.log` in the standard application data folder records internal diagnostics separately. Bind failures, oversized messages, client limits, idle disconnections, and file errors are shown in the window. File-write failure stops the receiver and reports the error. TCP messages over the configured limit close that client; oversized UDP datagrams are rejected. A bounded display queue applies backpressure to reception; UDP may lose datagrams under load. Stop finishes data already read by the receiver and flushes buffered partial TCP lines; clients should finish sending before Stop.

## Command-line overrides

```bash
python log_server.py --help
python log_server.py --version
```

`--host`, `--port`, and `--protocol` override saved socket settings for the current run. Saving Preferences persists the settings shown in the dialog.

## Tests

Run the complete suite, including real loopback sockets, file persistence/rotation, first-launch setup, Preferences, rich text, and Qt window lifecycle:

```bash
python -m unittest discover -s tests -v
```

Tests select Qt's offscreen platform by default and use temporary configuration, state, and log folders. They require permission to create TCP/UDP loopback sockets. Native appearance, firewall behavior, and device interoperability still require testing on the target operating systems.
