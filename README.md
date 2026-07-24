# Local AI Router

A **self-hosted OpenAI-compatible AI gateway** designed specifically for **local AI hosting**. It intelligently routes requests between multiple local `llama.cpp` model servers so that **only the model currently needed occupies GPU VRAM**.

The gateway presents a **single, stable OpenAI-compatible API endpoint**, allowing applications such as **Odysseus** to connect **without any modifications, plugins, or code changes**. From the client's perspective, it behaves exactly like a standard OpenAI API server while transparently selecting, starting, stopping, and proxying requests to the appropriate local model.

> This project was generated with multiple locally hosted llm models and agents.

---

## Why this project exists

Modern local LLM deployments often consist of multiple specialized models:

- Chat and instruction models
- Coding models
- Vision models
- Reasoning models

Keeping every model loaded simultaneously quickly exhausts GPU VRAM, even on high-end hardware.

**Local AI Router is built specifically to solve this problem.**

Instead of permanently loading every model, it intelligently manages local model servers by:

- keeping a lightweight routing model running continuously
- loading only the required model into GPU memory
- automatically unloading idle models after a configurable timeout
- exposing a single OpenAI-compatible endpoint regardless of which model is currently active

This allows a local AI workstation to host multiple large models while minimizing VRAM usage.

---

## Odysseus compatibility

One of the primary goals of this project is seamless integration with **Odysseus**.

Because the gateway implements the standard **OpenAI Chat Completions API**, **Odysseus works with it out of the box**.

**No modifications to Odysseus are required.**

Simply configure Odysseus to use the gateway's endpoint instead of a normal OpenAI-compatible server. The router handles all model selection and lifecycle management transparently, so Odysseus remains completely unaware that requests may be served by different local models.

The gateway is also compatible with applications that support the OpenAI-compatible Chat Completions API, as well as editor autocomplete clients that use the legacy `POST /v1/completions` endpoint.

---

## Project intention

The project is designed for **local AI workstations** where multiple specialized models share limited GPU resources.

Its primary objective is to **maximize available VRAM** by ensuring that only the model currently serving requests remains loaded.

The gateway provides:

- A single OpenAI-compatible endpoint for all clients.
- Full compatibility with Odysseus and other OpenAI-compatible applications.
- Intelligent request routing using a lightweight, persistent classifier.
- Automatic loading and unloading of local `llama.cpp` model servers.
- Optional local conversation persistence.
- Secure isolation of backend model servers from the network.

This enables users to switch seamlessly between chat, coding, reasoning, vision, or other specialized local models without permanently dedicating GPU memory to every model.

---

## Architecture

```text
Odysseus or another OpenAI-compatible client
                  |
                  | POST /v1/chat/completions
                  v
       AI Gateway (FastAPI, port 8000)
                  |
        +---------+----------+
        |                    |
        v                    v
LLM intent classifier    Explicit model selection
Qwen2.5-3B-Instruct      (chat or coder)
CPU, persistent                 |
        |                       |
        +----------+------------+
                   v
          Model lifecycle manager
                   |
          +--------+--------+
          |                 |
          v                 v
   Chat llama-server   Coder llama-server
   GPU, on demand      GPU, on demand
```

The classifier chooses a configured route when the client sends `"model": "auto"` or omits the model. An explicit `"model": "chat"` or `"model": "coder"` bypasses routing.

---

## Key behavior

- Presents one OpenAI-compatible API endpoint regardless of how many local models are available.
- Saves GPU VRAM by loading only the required answer model.
- Fully compatible with Odysseus without requiring any modifications.
- Supports any OpenAI-compatible client.
- Serializes model-serving requests, ensuring a model is never unloaded while generating a response.
- Keeps the CPU classifier running without consuming GPU VRAM.
- Automatically unloads idle models after a configurable timeout.
- Supports both normal and streaming OpenAI-compatible Chat Completions.
- Stores optional conversation sessions locally under `ai-gateway/sessions/` when the caller includes `X-Session-ID`.

---

## Project layout

```text
router/
├── README.md                 # Project overview
└── ai-gateway/
    ├── app.py                # FastAPI application entry point
    ├── config.yaml           # Models, routes, lifecycle, and security settings
    ├── api/                  # OpenAI, health, and tool endpoints
    ├── managers/             # Model lifecycle, sessions, routing
    ├── llm/                  # Classifier and upstream client
    ├── models/               # Model registry
    ├── tools/                # Optional allowlisted local tools
    └── sessions/             # Runtime conversation data (not committed)
```

---

## Security model

- Set `AI_GATEWAY_API_KEY` before starting the service.
- Bind all `llama.cpp` model servers to `127.0.0.1`.
- Keep `tools.enabled: false` unless local tooling is explicitly required.
- Place a TLS reverse proxy in front of the gateway before exposing it outside a trusted LAN.
- Grant the runtime account read access to model files and write access only to the session directory.

---

## Running the gateway

Refer to [the gateway README](ai-gateway/README.md) for Linux environment setup, model configuration, API-key configuration, and launch instructions.

Automatic chat routing supports `chat`, `coder`, and `image_gen`. Classified image requests use the configured chat model to refine the prompt before sending it to the image backend. The direct `POST /v1/images/generations` endpoint remains an unmodified passthrough without classification or refinement. See [the gateway README](ai-gateway/README.md) for configuration details.
