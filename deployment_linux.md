# Deployment Guide – Linux (Trixie)

This guide walks you through setting up and running the **AI Gateway** on a Linux system (e.g., Ubuntu 22.04, Debian 12, or Trixie). All paths below are absolute; replace `/home/tepxc/router` with the actual location of the repository on your machine.

---
## 1. Prerequisites

| Item | Minimum Version | Install Command |
|------|-----------------|-----------------|
| **Python** | 3.10+ | `sudo apt-get install python3.10 python3.10-venv` |
| **pip** | bundled with Python | – |
| **Git** | – | `sudo apt-get install git` |
| **Build tools** (optional, for native extensions) | – | `sudo apt-get install build-essential` |
| **Virtualenv** | – | `python3 -m pip install --user virtualenv` |

---
## 2. Clone the Repository

```bash
cd /home/tepxc
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

The gateway reads configuration from a `.env` file in the project root. Create it based on the template:

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

Ensure the directory exists and is writable by the user running the gateway:

```bash
sudo mkdir -p /var/lib/router/sessions
sudo chown $(whoami) /var/lib/router/sessions
```

---
## 5. Run the Gateway (Development Mode)

```bash
uvicorn ai_gateway.main:app --host 0.0.0.0 --port 8000 --reload
```

You should see output similar to:

```
INFO:     Started server process [12345]
INFO:     Waiting for application startup.
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

Test the readiness endpoint:

```bash
curl http://localhost:8000/ready
# Expected response: {"status":"ok"}
```

---
## 6. Run the Gateway (Production Mode)

For production, it’s recommended to use a process manager such as **systemd**.

### 6.1 Create a systemd Service File

```bash
sudo tee /etc/systemd/system/router.service > /dev/null <<'EOF'
[Unit]
Description=AI Gateway Service
After=network.target

[Service]
User=$(whoami)
WorkingDirectory=/home/tepxc/router
EnvironmentFile=/home/tepxc/router/.env
ExecStart=/home/tepxc/router/.venv/bin/uvicorn ai_gateway.main:app --host 0.0.0.0 --port 8000
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

### 6.2 Enable & Start the Service

```bash
sudo systemctl daemon-reload
sudo systemctl enable router.service
sudo systemctl start router.service
```

Check status:

```bash
sudo systemctl status router.service
```

---
## 7. Testing

Run the test suite to ensure everything is wired correctly:

```bash
pytest
```

All tests should pass. If you added new features, add corresponding tests.

---
## 8. Troubleshooting

| Symptom | Likely Cause | Fix |
|---------|--------------|-----|
| `uvicorn` fails to start with *Permission denied* | Wrong ownership of session dir or `.venv` | `sudo chown -R $(whoami) /home/tepxc/router` |
| Readiness endpoint returns *503* | Model server or image backend unreachable | Verify URLs in `.env` and that those services are running |
| ImportError: `pydantic` not found | Dependencies not installed | `pip install -r requirements.txt` |

---
## 9. Summary

1. Install prerequisites.
2. Clone repo & set up venv.
3. Configure `.env` and session directory.
4. Run locally or via systemd.
5. Verify with `/ready` and run tests.

Happy deploying!
