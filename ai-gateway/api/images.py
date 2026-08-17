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
import hashlib
import hmac
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TextIO
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from managers.model_manager import model_manager
from tools.image_tools import ImageGenerator
from .auth import authorize
from .models import ImageGenerationRequest

router = APIRouter()

# Configuration – fall back to defaults if not present.
image_config = model_manager.config.get("image_generation", {})
image_generator = ImageGenerator(image_config)
cleanup_seconds: int = int(image_config.get("cleanup_seconds", 86400))
canonical_base_url: str | None = image_config.get("base_url")
image_url_ttl_seconds: int = int(image_config.get("signed_url_ttl_seconds", 300))
image_url_signing_secret = image_config.get("signed_url_secret") or model_manager.config.get(
    "gateway", {}
).get("api_key")


@dataclass(frozen=True, slots=True)
class ImageResult:
    """Endpoint-neutral representation of one generated image."""

    created: int
    filename: str
    url: str
    path: Path


def _image_url(filename: str, base_url: str) -> str:
    """Build a short-lived, signed URL for a generated image."""
    if not isinstance(image_url_signing_secret, str) or not image_url_signing_secret:
        raise RuntimeError(
            "Image URL signing requires image_generation.signed_url_secret "
            "or gateway.api_key."
        )
    expires = int(time.time()) + image_url_ttl_seconds
    payload = f"{filename}:{expires}".encode("utf-8")
    signature = hmac.new(
        image_url_signing_secret.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()
    url_base = canonical_base_url or base_url
    query = urlencode({"expires": expires, "signature": signature})
    return f"{url_base.rstrip('/')}/v1/images/{filename}?{query}"


def _has_valid_image_signature(image_name: str, request: Request) -> bool:
    """Return whether a request carries a valid, unexpired image URL signature."""
    if not isinstance(image_url_signing_secret, str) or not image_url_signing_secret:
        return False
    try:
        expires = int(request.query_params["expires"])
        provided_signature = request.query_params["signature"]
    except (KeyError, TypeError, ValueError):
        return False
    if expires < int(time.time()):
        return False
    payload = f"{image_name}:{expires}".encode("utf-8")
    expected_signature = hmac.new(
        image_url_signing_secret.encode("utf-8"), payload, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(provided_signature, expected_signature)


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        await asyncio.wait_for(asyncio.shield(process.wait()), timeout=10)
    except TimeoutError:
        process.kill()
        await asyncio.shield(process.wait())

def _validate_image_size(size: str | None) -> str:
    return size or "1024x1024"


def _validate_images_response_format(response_format: str | None) -> Literal["url", "b64_json"]:
    response_format = response_format or "b64_json"
    if response_format not in {"url", "b64_json"}:
        raise HTTPException(status_code=400, detail="response_format must be 'url' or 'b64_json'.")
    return response_format


def make_images_response(
    image: ImageResult, response_format: Literal["url", "b64_json"]
) -> dict[str, Any]:
    """Serialize an ImageResult for the Images API only."""
    if response_format == "b64_json":
        encoded_image = base64.b64encode(image.path.read_bytes()).decode("ascii")
        return {"created": image.created, "data": [{"b64_json": encoded_image}]}
    return {"created": image.created, "data": [{"url": image.url}]}


async def generate_image(
    prompt: str, size: str | None, base_url: str
) -> ImageResult:
    """Generate an image and return an endpoint-neutral internal result."""
    size = _validate_image_size(size)

    process: asyncio.subprocess.Process | None = None
    log: TextIO | None = None
    output_file: Path | None = None
    try:
        # The request lock is held by the caller.  It first lets active
        # sessions finish, then releases every gateway-owned model's VRAM.
        await model_manager.release_models_for_image_generation()
        print(f"\n\nImage size received: {size}\n\n")
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

    assert output_file is not None
    return ImageResult(
        created=int(time.time()),
        filename=output_file.name,
        url=_image_url(output_file.name, base_url),
        path=output_file,
    )


async def stream_generate_image(
    prompt: str, size: str | None, base_url: str
) -> AsyncIterator[dict[str, Any]]:
    """Tee sd-cli output to the job log and yield progress plus an ImageResult.

    At most one fixed ``Generating image`` progress event is emitted every
    five seconds. All sd-cli output remains in the job log only.
    """
    size = _validate_image_size(size)
    process: asyncio.subprocess.Process | None = None
    log: TextIO | None = None
    read_task: asyncio.Task[bytes] | None = None
    try:
        await model_manager.release_models_for_image_generation()
        job = image_generator.prepare(prompt, size)
        log = job.log_file.open("w", encoding="utf-8")
        log.write(f"Started: {time.time()}\nOutput: {job.output_file}\n\n")
        log.flush()
        process = await asyncio.create_subprocess_exec(
            *job.command,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=job.environment,
        )
        assert process.stdout is not None
        deadline = time.monotonic() + job.timeout_seconds
        next_status_at = time.monotonic() + 5
        read_task = asyncio.create_task(process.stdout.readline())
        while True:
            now = time.monotonic()
            remaining = deadline - now
            if remaining <= 0:
                raise TimeoutError
            wait_seconds = min(remaining, max(0, next_status_at - now))
            done, _ = await asyncio.wait({read_task}, timeout=wait_seconds)
            if read_task in done:
                line = read_task.result()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace")
                log.write(text)
                log.flush()
                read_task = asyncio.create_task(process.stdout.readline())

            now = time.monotonic()
            if now >= next_status_at:
                yield {"type": "progress", "content": "<THINK>\nGenerating image.../THINK>"} # aligned to unsloth frontend but not sure others will work or not
                next_status_at = now + 5

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        await asyncio.wait_for(process.wait(), timeout=remaining)
        if process.returncode:
            raise RuntimeError(f"Image generation failed with exit code {process.returncode}. See {job.log_file}.")
        if not job.output_file.is_file():
            raise RuntimeError(f"Image generator exited successfully but did not create {job.output_file}.")
        yield {
            "type": "result",
            "image": ImageResult(
                created=int(time.time()),
                filename=job.output_file.name,
                url=_image_url(job.output_file.name, base_url),
                path=job.output_file,
            ),
        }
    except TimeoutError as error:
        if process is not None:
            await _stop_process(process)
        raise HTTPException(status_code=504, detail="Image generation timed out.") from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except OSError as error:
        raise HTTPException(status_code=502, detail=f"Unable to start image generator: {error}") from error
    finally:
        if read_task is not None and not read_task.done():
            read_task.cancel()
            try:
                await read_task
            except asyncio.CancelledError:
                pass
        if process is not None:
            await _stop_process(process)
        if log is not None:
            log.write(f"\nFinished: {time.time()}\nExit Code: {process.returncode if process else 'not started'}\n")
            log.close()


@router.post("/generations")
async def create_image(request: ImageGenerationRequest, raw_request: Request):
    authorize(raw_request)
    await model_manager.acquire_request()
    try:
        image = await generate_image(
            request.prompt,
            request.size,
            str(raw_request.base_url),
        )
        return make_images_response(
            image, _validate_images_response_format(request.response_format)
        )
    finally:
        model_manager.release_request()


@router.get("/{image_name}")
async def get_image(image_name: str, request: Request):
    if Path(image_name).name != image_name or not image_name.endswith(".png"):
        raise HTTPException(status_code=404, detail="Image not found.")
    if not _has_valid_image_signature(image_name, request):
        authorize(request)
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
