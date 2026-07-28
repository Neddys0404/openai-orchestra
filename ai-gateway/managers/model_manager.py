from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
import logging
from pathlib import Path
from typing import Any

import httpx
import yaml

from models.registry import ModelRegistry

logger = logging.getLogger(__name__)

class ModelManager:
    """Serializes GPU model switching and owns only processes it launches."""

    def __init__(self, config_path: str | Path | None = None):
        config_path = Path(config_path or Path(__file__).parents[1] / "config.yaml")
        self.config: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        gateway_config = self.config.setdefault("gateway", {})
        api_key_env = gateway_config.get("api_key_env")
        if api_key_env and not gateway_config.get("api_key"):
            gateway_config["api_key"] = os.getenv(api_key_env)
        self.request_timeout = float(gateway_config.get("request_timeout", 600))
        self.registry = ModelRegistry(self.config.get("models", {}), self.request_timeout)
        self.models = {name: self.registry.get(name) for name in self.registry.names()}
        self.active_model: str | None = None
        self.last_used: dict[str, float] = {}
        self._processes: dict[str, subprocess.Popen[Any]] = {}
        self.requests = 0
        # Request queue limit – configurable via gateway.max_concurrent_requests
        max_concurrent = int(self.config.get("gateway", {}).get("max_concurrent_requests", 10))
        self._request_semaphore = asyncio.BoundedSemaphore(max_concurrent)
        self.started_at = time.monotonic()
        self._request_lock = asyncio.Lock()

    def validate_configuration(self) -> None:
        gateway_config = self.config.get("gateway", {})
        if gateway_config.get("require_api_key") and not gateway_config.get("api_key"):
            env_name = gateway_config.get("api_key_env", "AI_GATEWAY_API_KEY")
            raise RuntimeError(f"Required gateway API key is missing. Set the {env_name} environment variable.")
        classifier_name = gateway_config.get("classifier_model")
        if classifier_name:
            self.registry.get(classifier_name)
        routes = self.config.get("routes", {})
        labels = gateway_config.get("classifier_labels")
        if labels is not None:
            if not isinstance(labels, list) or not all(isinstance(label, str) and label for label in labels):
                raise RuntimeError("gateway.classifier_labels must be a list of non-empty strings.")
            if set(labels) != set(routes):
                raise RuntimeError("gateway.classifier_labels must match configured route names.")
        for route_name, route in routes.items():
            if not isinstance(route, dict) or not route.get("model"):
                raise RuntimeError(f"Route '{route_name}' must declare a model.")
            self.registry.get(route["model"])
        refiner = self.config.get("prompt_refiner", {})
        if not isinstance(refiner, dict):
            raise RuntimeError("prompt_refiner must be a mapping.")
        if refiner.get("enabled", False):
            refiner_model = refiner.get("model")
            if not isinstance(refiner_model, str) or not refiner_model:
                raise RuntimeError("prompt_refiner.model must be configured when refinement is enabled.")
            if isinstance(routes.get(refiner_model), dict):
                refiner_model = routes[refiner_model].get("model")
            self.registry.get(refiner_model)
            prompt_file = refiner.get("system_prompt_file")
            if not isinstance(prompt_file, str) or not prompt_file:
                raise RuntimeError("prompt_refiner.system_prompt_file must be configured when refinement is enabled.")
            prompt_path = Path(prompt_file).expanduser()
            if not prompt_path.is_absolute():
                prompt_path = Path(__file__).parents[1] / prompt_path
            if not prompt_path.is_file():
                raise RuntimeError(f"Configured prompt refiner system prompt does not exist: {prompt_path}")

    async def acquire_request(self) -> None:
        """Acquire the global request lock and record a new request.

        The original implementation only incremented ``self.requests`` when a
        model became ready.  That meant the counter reflected *model start
        events* rather than actual API calls, which is misleading for
        metrics consumers.  We now increment the counter on every call to
        ``acquire_request`` – i.e. whenever a client request is admitted.
        """
        await self._request_lock.acquire()
        # Increment per-request counter.
        self.requests += 1

    def release_request(self, model_name: str | None = None) -> None:
        if model_name:
            self.last_used[model_name] = time.monotonic()
        if self._request_lock.locked():
            self._request_lock.release()

    async def get_endpoint(self, model_name: str) -> str:
        await self.ensure_ready(model_name)
        return self.registry.get(model_name).endpoint

    async def is_running(self, model_name: str) -> bool:
        model = self.registry.get(model_name)
        try:
            async with httpx.AsyncClient(timeout=2) as client:
                response = await client.get(model.endpoint + model.health_path)
            return response.is_success
        except httpx.HTTPError:
            return False

    async def ensure_ready(self, model_name: str) -> None:
        model = self.registry.get(model_name)
        if await self.is_running(model_name):
            self.active_model = model_name
            self.last_used[model_name] = time.monotonic()
            self.requests += 1
            return
        if not model.start_command:
            raise RuntimeError(f"Model '{model_name}' is unavailable at {model.endpoint}. Start its server or configure start_command.")
        for loaded_name in list(self._processes):
            if loaded_name != model_name and not self.registry.get(loaded_name).persistent:
                await self.unload_model(loaded_name)
        await self.unload_model(model_name)
        popen_options: dict[str, Any] = {"shell": True}
        if os.name == "nt":
            popen_options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_options["start_new_session"] = True

        logger.warning("Launching:\n%s", model.start_command)
        self._processes[model_name] = subprocess.Popen(model.start_command, **popen_options)
        process = self._processes[model_name]
        deadline = time.monotonic() + model.startup_timeout
        while time.monotonic() < deadline:
            await asyncio.sleep(1)
            if process.poll() is not None:
                self._processes.pop(model_name, None)
                raise RuntimeError(f"Model '{model_name}' exited during startup with code {process.returncode}.")
            if await self.is_running(model_name):
                self.active_model = model_name
                self.last_used[model_name] = time.monotonic()
                self.requests += 1
                return
        await self.unload_model(model_name)
        raise RuntimeError(f"Model '{model_name}' did not become ready within {model.startup_timeout:g} seconds.")

    async def shutdown(self) -> None:
        """Stop every process owned by this gateway during graceful shutdown."""
        await self.acquire_request()
        try:
            for model_name in list(self._processes):
                await self.unload_model(model_name)
        finally:
            self.release_request()

    async def unload_if_idle(self) -> None:
        if self._request_lock.locked():
            return
        async with self._request_lock:
            timeout = float(self.config.get("gateway", {}).get("idle_timeout_seconds", 600))
            now = time.monotonic()
            for model_name, last_used in list(self.last_used.items()):
                if not self.registry.get(model_name).persistent and now - last_used > timeout:
                    await self.unload_model(model_name)

    async def unload_nonpersistent_models(self) -> None:
        """Release gateway-owned GPU answer models for an external GPU workload."""
        for model_name in list(self._processes):
            if not self.registry.get(model_name).persistent:
                await self.unload_model(model_name)

    async def unload_model(self, model_name: str) -> bool:
        """Stop a gateway-owned process group, releasing the model's VRAM."""
        process = self._processes.pop(model_name, None)
        self.last_used.pop(model_name, None)
        if self.active_model == model_name:
            self.active_model = None
        if process is None or process.poll() is not None:
            return False
        if os.name == "nt":
            # Windows: use taskkill to terminate the process group
            await asyncio.to_thread(subprocess.run, ["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True, check=False)
        else:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.to_thread(process.wait, 10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
        # Ensure the process group is fully reaped
        try:
            await asyncio.to_thread(process.wait, 5)
        except Exception:
            pass
        return True

    def health(self) -> dict[str, Any]:
        return {
            "active_model": self.active_model,
            "managed_models": [name for name, process in self._processes.items() if process.poll() is None],
            "request_in_progress": self._request_lock.locked(),
            "requests": self.requests,
            "uptime_seconds": round(time.monotonic() - self.started_at, 1),
        }


model_manager = ModelManager()
