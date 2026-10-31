#!/usr/bin/env bash
# Start/stop one single-worker server: [PORT=8001] [PACK=librarian] scripts/server.sh start|stop|restart
# Port 8000 keeps data/server.pid and data/server.log; other ports get their own files.
cd "$(dirname "$0")/.." || exit 1
PORT=${PORT:-8000}
if [ "$PORT" = "8000" ]; then PID=data/server.pid; LOG=data/server.log; else PID=data/server-$PORT.pid; LOG=data/server-$PORT.log; fi
case "$1" in
  stop) [ -f "$PID" ] && kill "$(cat "$PID")" 2>/dev/null; rm -f "$PID" ;;
  start) if curl -sf "localhost:$PORT/health" >/dev/null; then echo "port $PORT is already serving; not starting"; exit 1; fi
         nohup .venv/bin/uvicorn app.main:app --port "$PORT" --workers 1 >> "$LOG" 2>&1 &
         echo $! > "$PID"
         for _ in $(seq 20); do curl -sf "localhost:$PORT/health" >/dev/null && { echo "server up on $PORT${PACK:+ (pack $PACK)}"; exit 0; }; sleep 0.5; done
         echo "server failed to start on $PORT"; tail -20 "$LOG"; exit 1 ;;
  restart) "$0" stop; sleep 1; "$0" start ;;
esac
