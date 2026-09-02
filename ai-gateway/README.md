**# AI Gateway**

An OpenAI-compatible gateway for one or more llama.cpp-compatible servers. It routes \`model: "auto"\` requests through a configured LLM classifier (with keyword fallback), proxies normal and Server-Sent Event streaming completions, saves optional conversation sessions, exposes health/metrics, and provides opt-in allowlisted Git/Docker tooling.

**## Run**

1\. Create and activate a Python 3.10+ virtual environment, then install dependencies:

   \`\`\`bash

   python3 -m venv .venv

   source .venv/bin/activate

   python -m pip install --upgrade pip

   python -m pip install -r requirements.txt

   \`\`\`

2\. Set the model endpoints (and optional \`start\_command\` values) in \`config.yaml\`.

3\. Copy the \*\_config\_example.yaml according to device's OS Type, currently supported Linux and MacOS.

   \`\`\`bash

   cp linux\_config\_example.yaml config.yaml

   \`\`\`

   or if you are using MacOS

   \`\`\`bash

   cp macos\_config\_example.yaml config.yaml

   \`\`\`

4\. Set a strong API key and start the gateway:

   \`\`\`bash

   export AI\_GATEWAY\_API\_KEY="replace-with-a-long-random-secret"

   uvicorn app\:app --host 0.0.0.0 --port 8000

   \`\`\`

Use \`POST /v1/chat/completions\`, just as with the OpenAI API. Specify \`chat\` or \`coder\`, or use \`auto\` to use the configured routes. Auto-routing supports \`chat\`, \`coder\`, and \`image\_gen\`; classified image requests are refined by the configured chat model before they are sent to the image backend. Send an \`X-Session-ID\` header to persist recent conversation context. API documentation is at \`/docs\`.

Editor autocomplete clients that use the legacy OpenAI endpoint are also supported through \`POST /v1/completions\`. These requests are forwarded unchanged to the selected llama.cpp server, including \`suffix\` and streaming fields. Because a text-completions request has no chat messages to classify, it must include an explicit configured model such as \`"model": "coder"\`.

Set \`gateway.classifier\_model\` to a configured model name. That model receives the available route names and the latest conversation turns, and must return one route name. If it cannot be reached or replies with an invalid route, the gateway uses the configured keyword fallback.



**## Run with Docker**

The gateway can also be run in Docker with CUDA-enabled \`llama.cpp\` and \`stable-diffusion.cpp\` (\`sd-cli\`). The container is configured to access the NVIDIA GPU, while local model files are mounted read-only from \`./models\`.

**### Requirements**

**### NVIDIA Container Toolkit setup (Ubuntu)**

For CUDA-enabled Docker containers, install the NVIDIA Container Toolkit on the **Ubuntu host/VM**, not inside the application container. The NVIDIA GPU driver must already be installed and working with `nvidia-smi`.

1. Add the NVIDIA Container Toolkit repository:

   ```bash
   curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey | \
       sudo gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg

   curl -s -L https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list | \
       sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' | \
       sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list

   sudo apt update
   ```

2. Install the toolkit:

   ```bash
   sudo apt install -y nvidia-container-toolkit
   ```

3. Configure Docker:

   ```bash
   sudo nvidia-ctk runtime configure --runtime=docker
   ```

4. Generate the NVIDIA CDI configuration:

   ```bash
   sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml
   ```

5. Verify CDI detects the GPU:

   ```bash
   sudo nvidia-ctk cdi list
   ```

6. Restart Docker:

   ```bash
   sudo systemctl restart docker
   ```

7. Verify GPU access from a CUDA container:

   ```bash
   sudo docker run --rm --gpus all \
       nvidia/cuda:13.1.1-runtime-ubuntu24.04 \
       nvidia-smi
   ```

The test container should report the same NVIDIA GPU visible from `nvidia-smi` on the Ubuntu host/VM. If the container cannot detect the GPU, fix the NVIDIA Container Toolkit/Docker configuration before building the CUDA-enabled `llama.cpp` image.

\- Docker with Docker Compose support

\- NVIDIA Container Toolkit

\- An NVIDIA GPU with a compatible driver

\- The project directory should contain \`Dockerfile\`, \`docker-compose.yml\`, \`ai-gateway/\`, and \`models/\`

**### Build the image**

Note: known good commit of llama.cpp = 9400c8946e4da5e7694f2c26d6d4e50e14b690fa
Note: known good commit of stable-diffusion.cpp = 6b3edaaf32cc19e5bb2d819c788bd557eddc8eba

From the project root:

\`\`\`bash

docker compose build

\`\`\`

The image builds \`llama.cpp\` and \`stable-diffusion.cpp\` with CUDA support. The first build may take some time.

**### Verify the container**

Before starting the gateway, verify GPU access and the model runtimes:

\`\`\`bash

docker compose run --rm ai-gateway nvidia-smi

docker compose run --rm ai-gateway llama-cli --version

docker compose run --rm ai-gateway llama-server --version

docker compose run --rm ai-gateway sd-cli --help

docker compose run --rm ai-gateway ls -lh /models

\`\`\`

**### Start the gateway**

Set the API key in the environment, or provide it through your \`.env\` file:

\`\`\`bash

export AI\_GATEWAY\_API\_KEY="replace-with-a-long-random-secret"

docker compose up -d

\`\`\`

Check the container status:

\`\`\`bash

docker compose ps

\`\`\`

View gateway logs:

\`\`\`bash

docker compose logs -f ai-gateway

\`\`\`

The OpenAI-compatible API is then available at:

\`\`\`text

http\://localhost:8000

\`\`\`

API documentation is available at:

\`\`\`text

http\://localhost:8000/docs

\`\`\`

**### Docker paths**

The host \`./models\` directory is mounted read-only as \`/models\` inside the container. Configure model paths in \`config.yaml\` using the container path, for example:

\`\`\`text

/models/your-model.gguf

\`\`\`

\`/app/ImageGen\` is a container-local directory created by the Docker image. It is **\*\*not\*\*** mounted to the host, so generated image files written there remain inside the container filesystem.

The \`./logs\` directory is mounted to \`/app/logs\`, so gateway logs written there remain available on the host.

**### Stop the gateway**

\`\`\`bash

docker compose down

\`\`\`

**## Idle VRAM release**

Set \`gateway.idle\_timeout\_seconds\` to the desired idle period. The gateway checks once per minute and stops model servers that it started through a model \`start\_command\`, which releases their VRAM. For safety, model servers you started yourself are not terminated by the gateway; configure their \`start\_command\` if you want lifecycle management.

When the gateway needs to start a different managed model, it also stops its currently managed non-persistent model first. This keeps a single answer model resident in VRAM at a time. Mark a small CPU classifier as \`persistent: true\` (as in the sample configuration) so it stays available without evicting the answer model.

Run a single Uvicorn worker. Each worker owns its own lifecycle state and multiple workers would conflict over the same model ports and processes.

**## Hosting safely**

Keep every llama.cpp server bound to \`127.0.0.1\`; the gateway is the only service that needs network exposure. It requires the \`AI\_GATEWAY\_API\_KEY\` environment variable by default. For access outside a trusted LAN, place the gateway behind a TLS reverse proxy and do not expose the model-server ports.

During graceful gateway shutdown, every model process started by the gateway is stopped and releases its VRAM. A \`start\_command\` that exits immediately now fails fast with its exit code instead of waiting for the whole startup timeout.

**## Start automatically with tmux**

Create a private environment file for the required key, then restrict it to your account:

\`\`\`bash

mkdir -p \~/.config/local-ai

printf 'AI\_GATEWAY\_API\_KEY="replace-with-a-long-random-secret"\n' > \~/.config/local-ai/gateway.env

chmod 600 \~/.config/local-ai/gateway.env

\`\`\`

Add this line to \`\~/.bashrc\` (change the path if you installed the project elsewhere):

\`\`\`bash

source \~/router/ai-gateway/scripts/ensure-local-ai.sh

\`\`\`

Each interactive Bash session then starts the gateway only if a tmux session called \`local-ai\` does not already exist. Inspect its live output with \`tmux attach -t local-ai\`, detach with \`Ctrl-b d\`, and read persistent output with \`tail -f \~/router/ai-gateway/logs/gateway.log\`.

The gateway never executes arbitrary user shell input. Tool execution is disabled by default and command names are allowlisted in \`config.yaml\`.

When enabled, \`POST /tool/git\` and \`POST /tool/docker\` accept a JSON body such as \`{"command": "status"}\`.

**## Image generation**

\`POST /v1/images/generations\` exposes the configured \`stable-diffusion.cpp\` Qwen Image runtime through the OpenAI Images API. The sample \`image\_generation\` configuration is populated from the local paths in this project; update it if your runtime paths differ. The gateway passes the prompt as one command argument (not through a shell), creates a PNG and log file in \`output\_directory\`, and waits for active API sessions to complete before stopping every model process it started, including persistent models, before running the job. Model servers started outside the gateway are never stopped because the gateway cannot safely manage them.

The classified \`image\_gen\` chat flow is separate from this direct endpoint. It loads or reuses \`prompt\_refiner.model\`, reads its system prompt from \`prompt\_refiner.system\_prompt\_file\`, and sends the refined prompt to the same image backend. Set \`fallback\_to\_original\_prompt: false\` to return an error when refinement fails. Direct \`POST /v1/images/generations\` requests are never classified or refined.

When image generation is enabled, its configured \`image\_generation.model\` ID and optional \`aliases\` are advertised by \`GET /v1/models\`. Clients such as Odysseus can select the ID as their **\*\*Image Model\*\***; it is not a chat-completion model. The sample configuration exposes \`gpt-image-1\` as an alias because Odysseus auto-detects that naming pattern, while the actual local runtime remains Qwen Image.

For example:

\`\`\`bash

curl http\://localhost:8000/v1/images/generations \\

  -H "Authorization: Bearer $AI\_GATEWAY\_API\_KEY" \\

  -H "Content-Type: application/json" \\

  -d '{"prompt":"a cozy reading nook in soft morning light","size":"1024x1024"}'

\`\`\`

The default response contains inline \`b64\_json\`, which works with clients that do not replay the gateway's authorization header for an image URL. Request \`{"response\_format":"url"}\` only when the client will send the bearer token while downloading the returned URL. Only one image per request is supported (\`n: 1\`).

Use the exported key as a bearer token in requests: \`Authorization: Bearer $AI\_GATEWAY\_API\_KEY\`.

The sample configuration uses a CPU-only profile because Qwen Image needs far more than 4 GB VRAM. It sets \`CUDA\_VISIBLE\_DEVICES\` empty and passes \`--offload-to-cpu\`, \`--clip-on-cpu\`, and \`--vae-on-cpu\` to \`sd-cli\`; generation will be substantially slower. For a machine with sufficient VRAM, set \`image\_generation.cpu\_only: false\` and configure the GPU/offload options for that machine.

**## Persistent Linux startup**

For a machine that should restart the gateway after a reboot or crash, install the user-level systemd service instead of relying only on the interactive-shell tmux helper:

\`\`\`bash

cd \~/router/ai-gateway

bash scripts/install-systemd-user-service.sh

loginctl enable-linger "$USER" # optional: keep it running after logout

\`\`\`

The installer uses the current checkout path and the same \`AI\_GATEWAY\_ENV\_FILE\` / \`\~/.config/local-ai/gateway.env\` credentials file as the tmux helper. Check it with \`systemctl --user status ai-gateway\` and logs with \`journalctl --user -u ai-gateway -f\`.

**## Persistent macOS startup**

On macOS, use the included \`launchd\` user agent. It starts at user login, restarts if the gateway exits, and keeps running after Terminal or SSH disconnects while that user session remains active:

\`\`\`bash

cd \~/router/ai-gateway

bash scripts/install-launchd-user-service.sh

\`\`\`

Check its state and live logs with:

\`\`\`bash

launchctl print "gui/$(id -u)/local.ai-gateway"

tail -f logs/launchd.out.log logs/launchd.err.log

\`\`\`

The gateway virtual environment is OS-specific: create a fresh \`.venv\` and install \`requirements.txt\` on the Mac. Update all \`config.yaml\` model, VAE, binary, and output paths to paths that exist on that Mac before installing the service. This is a user agent, so install it while logged in to the Mac desktop user using Terminal, not through SSH; it launches automatically at subsequent logins. A headless Mac requires a separate system LaunchDaemon rather than this user agent.

**## Persistent headless macOS startup**

For a Mac used as a headless server, install the system LaunchDaemon from an SSH session. It runs the gateway as the SSH user, restarts it after failure, and continues running after SSH disconnects and across reboots:

\`\`\`bash

cd \~/router/ai-gateway

sudo bash scripts/install-launchd-headless-service.sh

\`\`\`

Check the daemon and its logs with:

\`\`\`bash

sudo launchctl print system/local.ai-gateway

tail -f logs/launchd.out.log logs/launchd.err.log

\`\`\`

Do not also install the user LaunchAgent on the same machine; both services use port 8000.