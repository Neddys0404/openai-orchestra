"""Image generation endpoint.

This module implements the OpenAI‑compatible image generation API.  It uses
``ImageGenerator`` from :mod:`tools.image_tools` to run a stable‑diffusion
command line tool.  The implementation now:

* Uses a Pydantic request model for validation.
* Generates URLs based on a *canonical base URL* that can be configured via
  ``gateway.image_base_url`` in the gateway configuration.  If not set, the
  request's ``base_url`` is used.
* Periodically cleans up old image files and logs according to
  ``gateway.cleanup_seconds`` (default 24 h).
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from pathlib import Path
from typing import TextIO

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from managers.model_manager import model_manager
from tools.image_tools import ImageGenerator
from .auth import authorize

router = APIRouter()

# Configuration – fall back to defaults if not present.
image_config = model_manager.config.get("image_generation", {})
image_generator = ImageGenerator(image_config)
cleanup_seconds: int = int(image_config.get("cleanup_seconds", 86400))
canonical_base_url: str | None = image_config.get("base_url")


class ImageGenerationRequest(BaseModel):
    prompt: str = Field(min_length=1)
    n: int = 1
    size: str = "1024x1024"
    response_format: str = "b64_json"

    @staticmethod
    def validate_n(value: int) -> int:
        if value != 1:
            raise ValueError("Only n=1 is supported for image generation.")
        return value

    # Pydantic validator for the ``n`` field
    @staticmethod
    def _validate_n(cls, v: int) -> int:  # pragma: no cover - simple validation
        return ImageGenerationRequest.validate_n(v)


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(asyncio.shield(process.wait()), timeout=10)
    except TimeoutError:
        process.kill()
        await asyncio.shield(process.wait())

# TODO: image_gen route stuck here and return status code 400.
async def generate_image(prompt: str, size: str, response_format: str, base_url: str):

    print(f"\n\nResponse Format: {response_format}\n\n")

    if response_format not in {"url", "b64_json"}:
        raise HTTPException(status_code=400, detail="response_format must be 'url' or 'b64_json'.")

    process: asyncio.subprocess.Process | None = None
    log: TextIO | None = None
    output_file: Path | None = None
    try:
        # The diffusion runtime shares GPU resources with managed answer models.
        await model_manager.unload_nonpersistent_models()
        job = image_generator.prepare(prompt, size)
        output_file = job.output_file
        log = job.log_file.open("w", encoding="utf-8")
        log.write(f"Started: {time.time()}\nOutput: {job.output_file}\n\n")
        process = await asyncio.create_subprocess_exec(
            *job.command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=log,
            stderr=asyncio.subprocess.STDOUT,
            env=job.environment,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=job.timeout_seconds)
        except TimeoutError as error:
            await _stop_process(process)
            raise HTTPException(status_code=504, detail="Image generation timed out.") from error
        if process.returncode:
            raise RuntimeError(f"Image generation failed with exit code {process.returncode}. See {job.log_file}.")
        if not output_file.is_file():
            raise RuntimeError(f"Image generator exited successfully but did not create {output_file}.")
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=502, detail=f"Unable to start image generator: {error}") from error
    finally:
        if process is not None:
            await _stop_process(process)
        if log is not None:
            log.write(f"\nFinished: {time.time()}\nExit Code: {process.returncode if process else 'not started'}\n")
            log.close()

    if response_format == "b64_json":
        assert output_file is not None
        encoded_image = base64.b64encode(output_file.read_bytes()).decode("ascii")
        return {"created": int(time.time()), "data": [{"b64_json": encoded_image}]}
    assert output_file is not None
    # Use canonical base URL if configured, otherwise fall back to request base.
    url_base = canonical_base_url or base_url
    image_url = f"{url_base.rstrip('/')}/v1/images/{output_file.name}"
    return {"created": int(time.time()), "data": [{"url": image_url}]}


@router.post("/generations")
async def create_image(request: ImageGenerationRequest, raw_request: Request):
    authorize(raw_request)
    await model_manager.acquire_request()
    try:
        return await generate_image(
            request.prompt,
            request.size,
            request.response_format,
            str(raw_request.base_url),
        )
    finally:
        model_manager.release_request()


@router.get("/{image_name}")
async def get_image(image_name: str, request: Request):
    authorize(request)
    if Path(image_name).name != image_name or not image_name.endswith(".png"):
        raise HTTPException(status_code=404, detail="Image not found.")
    output_file = image_generator.output_directory() / image_name
    if not output_file.is_file():
        raise HTTPException(status_code=404, detail="Image not found.")
    return FileResponse(output_file, media_type="image/png", filename=image_name)


# ---------------------------------------------------------------------
# Background cleanup task
# ---------------------------------------------------------------------
async def _cleanup_task():
    """Periodically delete image files older than ``cleanup_seconds``.

    The task runs every hour.  It is started in the application lifespan
    function defined in :mod:`ai-gateway.app`.
    """
    while True:
        await asyncio.sleep(3600)
        cutoff = time.time() - cleanup_seconds
        for file in image_generator.output_directory().glob("*.png"):
            try:
                if file.stat().st_mtime < cutoff:
                    file.unlink()
            except OSError:
                pass
