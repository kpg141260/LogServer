# LogServer help

LogServer receives TCP and UDP log messages, displays them in client tabs, and can save them to log files. You can also open existing log files and merge two files by timestamp.

## Start and stop receiving

1. Open **File > Preferences...** and select the listener address, port and protocol on the **Socket** tab.
2. Select **Start** in the toolbar or File menu. The endpoint above the logs shows the listening address.
3. Select **Stop** to stop receiving and finish pending log writes.

The default address, `127.0.0.1`, accepts clients on the same computer. Use `0.0.0.0` to accept IPv4 devices on your network, and allow the chosen port through your firewall. Use this receiver on a trusted network; connections have no authentication or encryption.

TCP clients send newline-delimited messages. UDP clients send one message per datagram. Plain text, JSON messages and syslog text are supported. In JSON, use `message` or `msg` for the message, and optionally `level` or `severity` for its severity.

Socket and file-logging preferences take effect the next time you start the server. Appearance and display preferences apply immediately.

## Socket and file panels

The **All clients** tab shows received messages together. Each client also has a separate tab. TCP tab labels show whether the client is connected. Disconnecting a client or stopping the server preserves its displayed messages.

Close a client tab with its close button. **All clients** stays open. A client that sends more messages can recreate its tab.

Use **View > Socket panel** and **View > File panel** to show or hide each panel. Hiding a panel preserves its contents and does not stop reception. Click a log pane or client tab to make it the target of Search, Filter, Clear display and Line numbers.

**Clear display** clears the active log view. It does not delete saved files. Display history is limited by **Display line limit** in Preferences; saved logs are independent of this limit.

## Search

Type into **Search** in the status bar to highlight literal text in the active pane. Enable **Match case** when capitalization matters. Use the adjacent buttons for the first, previous, next and last visible match. Next and previous wrap around the visible results.

Use the platform's Find shortcut, usually **Ctrl+F** on Linux and Windows or **Command+F** on macOS, to focus Search. Large-file matches and highlighting update in the background while you type. Each pane retains its own search text and case setting.

## Filter messages

Open **Filter** in the toolbar to choose a severity, IP address, protocol or device. Select **All** within a category to clear that category. Filters combine, so a line must satisfy every active category.

Choose **Filter > Keyword...** to enter one or more comma-separated keywords. A line passes when it contains any keyword, ignoring capitalization. Keywords are literal text; punctuation is not a regular expression. An empty keyword field clears this filter.

Keyword filtering combines with the other categories. The number beside Filter counts active categories. **Reset filters** restores all lines in the active pane. Each pane retains its own filters, and filters do not alter saved files.

The keywords in **Preferences > Appearance and startup** add color emphasis. Use **Filter > Keyword...** to hide nonmatching lines.

## Open and close a log file

Choose **File > Open log file...** and select a `.log` file. It opens in the file panel while socket reception continues. Large files display the last configured number of lines; the panel reports omitted older lines.

Choose **File > Close log file** to clear the file previews and hide the file panel. This does not delete or modify the original file.

## Merge two log files

1. Choose **File > Merge log files...**, then select the first and second files.
2. Review the two previews in the file panel. Click either preview to search or filter it.
3. Select **Merge...** beneath the previews and choose an output file.

The merge reads the full source files, orders records by timestamp, and preserves continuation lines and duplicate records. Preview limits and filters do not restrict the output. The output cannot overwrite either source file. Invalid timestamps or file errors are reported in the window.

## Appearance and saved logs

**File > Preferences...** controls appearance, fonts, display limits, listener settings and received-log files. The **Files** tab enables file logging and selects the folder, combined or per-client files, and rotation settings. Existing files are appended.

The **Log Format** tab previews saved records as you toggle originator time (`[DTS]`), local reception time (`[LTS]`), device IP and protocol. Originator time and IP are enabled by default; local time and protocol are disabled. Both timestamps can be selected together. Severity appears once as `**SEVERITY** - message`. Missing originator timestamps are omitted. Changes apply at the next Start without rewriting existing files. Records without timestamps cannot be merged by timestamp.

The left side of the status bar shows server state, TCP client count, received-message count and the colored application diagnostic level. This level controls internal diagnostics; received messages of every severity can still be displayed and saved. Internal diagnostics are recorded separately in `application.log` in the operating system's application data folder.

Use **View > Line numbers** to toggle line numbers for the active pane. Search, filters and line numbers affect the display only.

## Errors and application information

Listener and file errors appear above the log panes. If the listener cannot bind, check the address and whether another process uses the port. After changing listener settings, Stop and Start the server. A received-log write failure stops reception; check the log folder and available storage before restarting.

**Help > Help** opens this guide. **Help > About** shows application details and the current version read from the version manifest.
