# Local AI Gateway

A high-performance, OpenAI-compatible gateway designed for intelligent routing across multiple local and remote LLM servers. The gateway optimizes VRAM usage by automatically managing model lifecycles—loading required models on demand and unloading idle ones to ensure maximum available GPU memory for your active tasks.

> This project was generated with multiple locally hosted llm models and agents.

---

## 🚀 Key Features

### 🧠 Intelligent Auto-Routing
When configured with `model: "auto"`, the gateway uses a lightweight, persistent LLM classifier to analyze incoming requests and automatically route them to the most appropriate specialized model (e.g., switching between a **Chat** model, a **Coding** model, or an **Image Generation** prompt refiner).

### 💾 Intelligent VRAM Management
- **On-Demand Loading**: Automatically starts local model servers (like `llama.cpp`) when a request arrives for a specific model.
- **Idle Unloading**: Automatically stops and unloads models that have been idle longer than the configured `idle_timeout_seconds`.
- **Persistent Models**: Mark lightweight classifier models as `persistent: true` to keep them resident in memory, ensuring routing is always available without evicting answer models.

### 🔌 Full OpenAI Compatibility
Seamlessly integrates with any application that supports the OpenAI API standard (e.g., Odysseus, AutoGPT, or custom Python scripts) without requiring code changes.
- **Chat Completions**: Supports both standard and streaming responses.
- **Text Completions**: Supports legacy `POST /v1/completions` for editor autocomplete clients.
- **Models Discovery**: Standard `/v1/models` endpoint that includes automatically discovered image models.

### 🎨 Advanced Image Generation
Includes a specialized workflow for diffusion models:
- **Prompt Refinement**: Uses an LLM to expand or improve user prompts before sending them to the image backend.
- **SD-CLI Integration**: Directly manages local stable diffusion command-line interfaces.
- **Image Model Aliasing**: Automatically advertises specific model IDs (e.g., `gpt-image-1`) to ensure compatibility with client applications.

### 🛠️ Experimental Tool System
Allowlist-based execution of local system tools:
- **Git Support**: Execute status, diff, and log commands via API.
- **Docker Support**: Manage containers through the gateway.

---

## 🏗️ Architecture

The request flow is designed for minimal latency and maximum intelligence:

```text
                  Odysseus or another OpenAI-compatible client
                               |
                +--------------+---------------+-----------------+
                |                              |                 |
          Chat / Coder API               Image Gen API       Other APIs
         (v1/chat/...)                (v1/images/...)     (/health, /tools)
                |                              |                 |
                v                              v                 v
      +-----------------------+      +-------------------+  [Passthrough]
      |                       |      |                   |
      |    LLM Classifier     |      | Prompt Refiner    |
      |   (CPU, persistent)   |      | (via Chat Model)  |
      |                       |      |                   |
      +-----------+-----------+      +---------+---------+
                  |                            |
                  +------------+               |
                               |               |
                    Model Lifecycle Manager    |
                               |               |
                +--------------+---------------+
                |              |               |
                v              v               v
           Chat Server     Coder Server     Image Backend
           (llama.cpp)      (llama.cpp)     (sd-cli/diff)
           GPU, on demand  GPU, on demand   GPU, on demand
```

---

## 📦 Installation & Running

### Prerequisites
- **Python 3.10+**
- **llama.cpp** (or other OpenAI-compatible local servers) installed and available in your path.
- **Stable Diffusion CLI** (optional, for image generation).

### Setup
1.  **Create a Virtual Environment**:
    ```bash
    python -m venv .venv
    # Windows:
    .venv\Scripts\activate
    # Linux/macOS:
    source .venv/bin/activate
    ```

2.  **Install Dependencies**:
    ```bash
    pip install -r ai-gateway/requirements.txt
    ```

3.  **Configure the Gateway**:
    Copy `ai-gateway/config_example.yaml` to `ai-gateway/config.yaml` and update your model endpoints, paths, and start commands.

4.  **Set Environment Variables**:
    The gateway requires an API key for secure network exposure:
    ```bash
    # Linux/macOS
    export AI_GATEWAY_API_KEY="your-secure-secret-here"
    # Windows (PowerShell)
    $env:AI_GATEWAY_API_KEY="your-secure-secret-here"
    ```

5.  **Launch**:
    ```bash
    uvicorn ai-gateway.app:app --host 0.0.0.0 --port 8000
    ```

---
## ⚙️ Configuration (`config.yaml`)

The gateway is highly configurable via a YAML file. Key sections include:

| Section | Purpose | Example Use Case |
| :--- | :--- | :--- |
| `gateway` | Core runtime settings | Setting `idle_timeout_seconds` or the API key environment variable name. |
| `models` | Registry of all available models | Defining endpoints, startup commands for local servers, and persistence status. |
| `routes` | Mapping intent to models | Assigning keywords (e.g., "code", "draw") to specific model IDs. |
| `prompt_refiner` | LLM-based prompt enhancement | Configuring the LLM used to improve image generation prompts. |
| `image_generation`| SD-CLI and Diffusion settings | Setting paths to `.gguf` models, VAEs, and CLIP weights. |
| `tools` | Experimental tool access | Enabling/disabling Git or Docker tool execution. |

---

## 📡 API Endpoints

### OpenAI Compatible
*   `POST /v1/chat/completions`: Primary endpoint for chat interactions (supports streaming).
*   `POST /v1/completions`: Legacy text completion for editor autocomplete clients.
*   `GET  /v1/models`: Lists all available models, including automatically discovered image models.
*   `POST /v1/images/generations`: Triggers the image generation workflow (SD-CLI).

### System & Experimental
*   `GET  /health`: Returns status of the gateway and managed processes.
*   `POST /tool/{tool_name}`: Executes allowlisted commands (e.g., `git`, `docker`).

---

## 🤖 Models

The gateway provides intelligent model management for both local and remote models.

### Local vs Remote
- **Local Models**: Managed via the `start_command` in `config.yaml`. The gateway handles starting the processes and releasing their VRAM when idle.
- **Remote Models**: Any standard OpenAI-compatible endpoint can be added as a model provider.

### Intelligent Routing (`model: "auto"`)
When `model` is set to `"auto"` (or omitted), the gateway uses its internal LLM classifier to determine the intent of the request and routes it to the most appropriate configured route.

### Model Lifecycle & Persistence
- **Automatic Loading**: Models are launched on demand when first requested.
- **Idle Unloading**: Managed models that have not been used for a configurable period (`idle_timeout_seconds`) are automatically shut down to free up GPU VRAM.
- **Persistence**: By setting `persistent: true` in the configuration, you can keep essential models (like the classifier) resident in memory at all times.

---

## 🛠️ Tool System (Experimental)

The Tool System allows you to extend your AI workflow with local system commands. Currently experimental and require explicit enablement in the configuration file.

| Tool | Allowed Commands (Configurable) |
| :--- | :--- |
| **Git** | `status`, `diff`, `log`, `branch` |
| **Docker** | `ps`, `images` |

*Note: Tools are executed as subprocesses and require the gateway to have appropriate system permissions.*

---

## ⚠️ Limitations & Troubleshooting

### Known Limitations
*   **Experimental Tools**: The Tool System is currently in development and should be used with caution.
*   **Image Generation Performance**: Running SD-CLI on CPU is significantly slower; high-performance setups require dedicated GPU allocation for the diffusion process.

### Common Issues
*   **401 Unauthorized**: Ensure `AI_GATEWAY_API_KEY` matches your request's Bearer token.
*   **503 Service Unavailable**: Occurs if a model fails to start within its configured `startup_timeout`. Check the gateway logs for startup command errors.
*   **VRAM Exhaustion**: If models are not unloading, verify that they were started via the `start_command` in `config.yaml` so the gateway can manage their lifecycle.

---