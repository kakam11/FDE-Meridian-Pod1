#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PORT=8501

# Load API key from .env
if [[ ! -f "$SCRIPT_DIR/.env" ]]; then
  echo "ERROR: .env file not found at $SCRIPT_DIR/.env"
  exit 1
fi
export $(grep -v '^#' "$SCRIPT_DIR/.env" | xargs)

# Kill any process already on the port
if lsof -ti:$PORT &>/dev/null; then
  echo "Stopping existing process on port $PORT..."
  lsof -ti:$PORT | xargs kill -9
  sleep 1
fi

# Suppress Streamlit email prompt on first run
mkdir -p ~/.streamlit
if [[ ! -f ~/.streamlit/credentials.toml ]]; then
  echo '[general]' > ~/.streamlit/credentials.toml
  echo 'email = ""' >> ~/.streamlit/credentials.toml
fi

# Find Python with Streamlit
PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" &>/dev/null && "$candidate" -c "import streamlit" &>/dev/null; then
    PYTHON="$candidate"
    break
  fi
done

if [[ -z "$PYTHON" ]]; then
  echo "ERROR: No Python with Streamlit found. Run: pip install streamlit"
  exit 1
fi

echo "Starting Reconciliation Agent on http://localhost:$PORT"
echo "Python: $($PYTHON --version)  |  Streamlit: $($PYTHON -m streamlit version | head -1)"
echo "Logs: /tmp/streamlit.log"
echo ""

"$PYTHON" -m streamlit run "$SCRIPT_DIR/pod1/app.py" \
  --server.port $PORT \
  --server.headless true \
  > /tmp/streamlit.log 2>&1 &

echo "PID: $!  (run 'kill $!' to stop)"

# Wait for the server to respond
for i in {1..15}; do
  if curl -s -o /dev/null -w "%{http_code}" http://localhost:$PORT | grep -q "200"; then
    echo "App is up → http://localhost:$PORT"
    exit 0
  fi
  sleep 1
done

echo "App did not respond after 15s — check /tmp/streamlit.log"
exit 1
