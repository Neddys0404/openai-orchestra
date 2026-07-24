# Deployment Guide – macOS

This guide walks you through setting up and running the **AI Gateway** on macOS (any recent version, e.g., Monterey, Ventura, or Sonoma). All paths below are absolute; replace `/Users/tepxc/router` with the actual location of the repository on your machine.

---
## 1. Prerequisites

| Item | Minimum Version | Install Command |
|------|-----------------|-----------------|
| **Homebrew** | – | `ruby -e "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` |
| **Python** | 3.10+ | `brew install python@3.10` |
| **Git** | – | `brew install git` |
| **Build tools** (optional, for native extensions) | – | `xcode-select --install` |
| **Virtualenv** | – | `pip3 install --user virtualenv` |

---
## 2. Clone the Repository

```bash
cd /Users/tepxc
git clone https://github.com/your-org/router.git
cd router
```

---
## 3. Create a Virtual Environment & Install Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
# Optional: install dev/test dependencies
pip install -r dev-requirements.txt
```

---
## 4. Configure Environment Variables

Create a `.env` file based on the template:

```bash
cp .env.example .env
# Edit .env to set your values
```

Typical variables:

| Variable | Description |
|----------|-------------|
| `AI_GATEWAY_API_KEY` | Secret key used for authenticating clients |
| `MODEL_SERVER_URL` | Base URL of the model inference server |
| `IMAGE_BACKEND_URL` | Base URL of the image generation backend |
| `SESSION_DIR` | Directory where session files are stored (default: `/var/lib/router/sessions`) |

Create the session directory and set permissions:

```bash
sudo mkdir -p /var/lib/router/sessions
sudo chown $(whoami) /var/lib/router/sessions
```

---
## 5. Run the Gateway (Development Mode)

```bash
uvicorn ai_gateway.main:app --host 0.0.0.0 --port 8000 --reload
```

Verify readiness:

```bash
curl http://localhost:8000/ready
# Expected response: {"status":"ok"}
```

---
## 6. Run the Gateway (Production Mode)

On macOS, a common approach is to use **launchd**. Create a plist file in `~/Library/LaunchAgents`.

### 6.1 Create the LaunchAgent

```bash
cat <<'EOF' > ~/Library/LaunchAgents/com.tepxc.router.plist
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.tepxc.router</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/tepxc/router/.venv/bin/uvicorn</string>
        <string>ai_gateway.main:app</string>
        <string>--host</string>
        <string>0.0.0.0</string>
        <string>--port</string>
        <string>8000</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/Users/tepxc/router</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>ENV_FILE</key>
        <string>/Users/tepxc/router/.env</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/tepxc/router/logs/router.out.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/tepxc/router/logs/router.err.log</string>
</dict>
</plist>
EOF
```

Create the log directory:

```bash
mkdir -p /Users/tepxc/router/logs
```

### 6.2 Load the Agent

```bash
launchctl load ~/Library/LaunchAgents/com.tepxc.router.plist
# To start immediately
launchctl start com.tepxc.router
```

Check status:

```bash
launchctl list | grep com.tepxc.router
```

---
## 7. Testing

Run the test suite:

```bash
pytest
```

All tests should pass.

---
## 8. Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `uvicorn` fails with *Permission denied* | Wrong ownership of session dir or `.venv` | `sudo chown -R $(whoami) /Users/tepxc/router` |
| Readiness endpoint returns *503* | Model server or image backend unreachable | Verify URLs in `.env` and that those services are running |
| ImportError: `pydantic` not found | Dependencies not installed | `pip install -r requirements.txt` |

---
## 9. Optional – Docker Deployment

If you prefer containerization, a minimal Dockerfile is provided in the repo root. Build and run:

```bash
docker build -t router:latest .
docker run -d -p 8000:8000 --env-file .env router:latest
```

---
## 10. Summary

1. Install Homebrew, Python, and Git.
2. Clone repo & set up venv.
3. Configure `.env` and session directory.
4. Run locally or via launchd.
5. Verify with `/ready` and run tests.

Happy deploying!
