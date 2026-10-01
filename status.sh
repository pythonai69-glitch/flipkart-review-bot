#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

PID=$(pgrep -f "python.*bot.py")

if [ -n "$PID" ]; then
    echo "🟢 Status: RUNNING (PID: $PID)"
    echo "🌐 Local Dashboard: http://localhost:8080"
    echo "---------------- Recent Logs ----------------"
    if [ -f "bot.log" ]; then
        tail -n 15 bot.log
    fi
else
    echo "🔴 Status: STOPPED"
    echo "Start it using: ./start.sh"
fi
