#!/bin/bash

echo "Starting EasyEcom AI Assistant..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

TERMINAL=$(which gnome-terminal 2>/dev/null || which xterm 2>/dev/null || which konsole 2>/dev/null)
if [ -z "$TERMINAL" ]; then
    echo "No supported terminal emulator found"
    exit 1
fi

open_terminal() {
    if [[ "$TERMINAL" == *"gnome-terminal"* ]]; then
        gnome-terminal -- bash -c "$1; exec bash"
    else
        $TERMINAL -e "bash -c '$1; exec bash'"
    fi
}

echo "Starting Mock API server..."
open_terminal "cd '$SCRIPT_DIR' && python -m uvicorn mock_apis.easyecom_mock:mock_app --host 0.0.0.0 --port 8001"

sleep 3

echo "Starting Main API server..."
open_terminal "cd '$SCRIPT_DIR' && python main.py"

sleep 3

echo "Starting Streamlit UI..."
open_terminal "cd '$SCRIPT_DIR' && streamlit run streamlit_ui.py"

echo ""
echo "All services started!"
echo "- Mock API: http://localhost:8001"
echo "- Main API: http://localhost:8000"
echo "- Streamlit UI: http://localhost:8501"
