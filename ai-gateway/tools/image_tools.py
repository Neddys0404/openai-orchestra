from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


@dataclass(frozen=True)
class ImageJob:
    command: list[str]
    environment: dict[str, str]
    output_file: Path
    log_file: Path
    timeout_seconds: float


class ImageGenerator:
    """Builds one configured stable-diffusion.cpp image job without a shell."""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def _required_path(self, name: str) -> Path:
        value = self.config.get(name)
        if not isinstance(value, str) or not value:
            raise ValueError(f"image_generation.{name} must be configured.")
        path = Path(value).expanduser()
        if not path.is_file():
            raise RuntimeError(f"Configured {name} does not exist: {path}")
        return path

    def output_directory(self) -> Path:
        value = self.config.get("output_directory")
        if not isinstance(value, str) or not value:
            raise ValueError("image_generation.output_directory must be configured.")
        directory = Path(value).expanduser()
        directory.mkdir(parents=True, exist_ok=True)
        return directory

    def validate_configuration(self) -> None:
        if not self.config.get("enabled", False):
            return
        sd_cli = self._required_path("sd_cli")
        if not os.access(sd_cli, os.X_OK):
            raise RuntimeError(f"Configured sd_cli is not executable: {sd_cli}")
        for name in ("diffusion_model", "vae", "llm"):
            self._required_path(name)
        self.output_directory()
        allowed_sizes = self.config.get("allowed_sizes", ["1024x1024"])
        if not isinstance(allowed_sizes, list) or not all(isinstance(value, str) for value in allowed_sizes):
            raise ValueError("image_generation.allowed_sizes must be a list of strings.")
        float(self.config.get("timeout_seconds", 900))

    def prepare(self, prompt: str, size: str) -> ImageJob:
        if not self.config.get("enabled", False):
            raise ValueError("Image generation is disabled in configuration.")
        if not prompt.strip():
            raise ValueError("'prompt' must not be empty.")
        if len(prompt) > int(self.config.get("max_prompt_characters", 8_000)):
            raise ValueError("'prompt' is too long.")
        allowed_sizes = self.config.get("allowed_sizes", ["1024x1024"])
        if size not in allowed_sizes:
            raise ValueError(f"'size' must be one of: {', '.join(allowed_sizes)}.")

        try:
            width, height = (int(value) for value in size.lower().split("x", 1))
        except (TypeError, ValueError):
            raise ValueError("'size' must be formatted as WIDTHxHEIGHT.") from None
        if width <= 0 or height <= 0:
            raise ValueError("'size' dimensions must be positive.")

        output_directory = self.output_directory()
        log_directory = output_directory / "logs"
        log_directory.mkdir(parents=True, exist_ok=True)
        job_id = f"qwen_{datetime.now(timezone.utc):%Y%m%d_%H%M%S}_{uuid4().hex[:8]}"
        output_file = output_directory / f"{job_id}.png"
        log_file = log_directory / f"{job_id}.log"
        command = [
            str(self._required_path("sd_cli")),
            "--diffusion-model", str(self._required_path("diffusion_model")),
            "--vae", str(self._required_path("vae")),
            "--llm", str(self._required_path("llm")),
            "--cfg-scale", str(self.config.get("cfg_scale", 2.5)),
            "--sampling-method", str(self.config.get("sampling_method", "euler")),
            "--steps", str(self.config.get("steps", 40)),
            "-H", str(height),
            "-W", str(width),
            "--flow-shift", str(self.config.get("flow_shift", 3)),
            "-p", prompt,
            "-o", str(output_file),
        ]
        if self.config.get("offload_to_cpu", False):
            command.append("--offload-to-cpu")
        if self.config.get("clip_on_cpu", False):
            command.append("--clip-on-cpu")
        if self.config.get("vae_on_cpu", False):
            command.append("--vae-on-cpu")
        threads = self.config.get("threads")
        if threads is not None:
            command.extend(["--threads", str(threads)])
        environment = os.environ.copy()
        if self.config.get("cpu_only", False):
            environment["CUDA_VISIBLE_DEVICES"] = ""
        else:
            cuda_visible_devices = self.config.get("cuda_visible_devices")
            if cuda_visible_devices is not None:
                environment["CUDA_VISIBLE_DEVICES"] = str(cuda_visible_devices)
        if self.config.get("auto_fit") is not None:
            command.extend(["--auto-fit", "on" if self.config["auto_fit"] else "off"])

        if self.config.get("max_vram_gib") is not None:
            command.extend(["--max-vram", str(self.config["max_vram_gib"])])
        return ImageJob(command, environment, output_file, log_file, float(self.config.get("timeout_seconds", 900)))
