#!/usr/bin/env bash
# Start/stop the single-worker dev server: scripts/server.sh start|stop|restart
cd "$(dirname "$0")/.." || exit 1
PID=data/server.pid
case "$1" in
  stop) [ -f $PID ] && kill "$(cat $PID)" 2>/dev/null; rm -f $PID ;;
  start) nohup .venv/bin/uvicorn app.main:app --port 8000 --workers 1 >> data/server.log 2>&1 &
         echo $! > $PID
         for _ in $(seq 20); do curl -sf localhost:8000/health >/dev/null && { echo "server up"; exit 0; }; sleep 0.5; done
         echo "server failed to start"; tail -20 data/server.log; exit 1 ;;
  restart) "$0" stop; sleep 1; "$0" start ;;
esac
