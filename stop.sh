#!/bin/bash
PID=$(pgrep -f "python.*bot.py")

if [ -z "$PID" ]; then
    echo "ℹ️ No running Flipkart Review Bot process found."
else
    echo "🛑 Stopping Flipkart Review Bot (PID: $PID)..."
    kill $PID
    sleep 1
    if pgrep -f "python.*bot.py" > /dev/null; then
        kill -9 $PID
    fi
    echo "✅ Bot and Local Server stopped."
fi
