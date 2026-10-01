#!/bin/bash
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Check if already running
PID=$(pgrep -f "python.*bot.py")
if [ -n "$PID" ]; then
    echo "⚠️ Flipkart Review Bot is already running with PID: $PID"
    echo "🌐 Local Dashboard: http://localhost:8080"
    exit 0
fi

echo "🚀 Starting Flipkart Review Bot & Local Server..."
nohup ./.venv/bin/python bot.py > bot.log 2>&1 &
NEW_PID=$!
sleep 2

if ps -p $NEW_PID > /dev/null; then
    echo "✅ Bot & Local Server started successfully! (PID: $NEW_PID)"
    echo "🌐 Local Web Dashboard: http://localhost:8080"
    echo "📄 Live Logs: tail -f bot.log"
else
    echo "❌ Failed to start. Check bot.log for errors:"
    cat bot.log
fi
