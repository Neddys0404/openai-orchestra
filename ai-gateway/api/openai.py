from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
import httpx

from api.auth import authorize
from llm.client import UpstreamClient
from llm.prompt_refiner import ImagePromptRefiner
from api.images import generate_image
from .models import ChatCompletionRequest, CompletionRequest, ImageGenerationRequest, Message
from tools.tool_router import ToolRouter
from managers.model_manager import model_manager
from managers.router_manager import RouterManager
from managers.session_manager import SessionManager

router = APIRouter()
client = UpstreamClient()
logger = logging.getLogger(__name__)
gateway_config = model_manager.config.get("gateway", {})
router_manager = RouterManager(
    model_manager.config.get("routes", {}),
    model_manager.registry,
    gateway_config.get("classifier_model", "chat"),
    gateway_config.get("classifier_timeout", 20),
)
prompt_refiner = ImagePromptRefiner(model_manager.config.get("prompt_refiner", {}), client)
tool_router = ToolRouter(model_manager.config.get("tools", {}))
session_directory = Path(gateway_config.get("session_directory", "sessions"))
if not session_directory.is_absolute():
    session_directory = Path(__file__).parents[1] / session_directory
session_manager = SessionManager(str(session_directory), gateway_config.get("max_recent_messages", 20))


def _authorize(request: Request) -> None:
    api_key = gateway_config.get("api_key")
    if api_key and request.headers.get("authorization") != f"Bearer {api_key}":
        raise HTTPException(status_code=401, detail="Invalid API key")


def _prompt_from_message(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item["text"]
            for item in content
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
        )
    return ""


def _stream_content(chunk: bytes, buffer: bytes) -> tuple[bytes, list[str], bool]:
    buffer += chunk
    content: list[str] = []
    complete = False
    while b"\n" in buffer:
        line, buffer = buffer.split(b"\n", 1)
        payload = line.strip().removeprefix(b"data:").strip()
        if not line.strip().startswith(b"data:"):
            continue
        if payload == b"[DONE]":
            complete = True
            continue
        try:
            delta = json.loads(payload).get("choices", [{}])[0].get("delta", {})
            if isinstance(delta.get("content"), str):
                content.append(delta["content"])
        except (json.JSONDecodeError, IndexError, TypeError, AttributeError):
            continue
    return buffer, content, complete


async def _stream_response(
    endpoint: str,
    body: dict[str, Any],
    timeout: float,
    session_id: str | None,
    original_messages: list[dict[str, Any]],
    model_name: str,
):
    buffer = b""
    parts: list[str] = []
    completed = False
    try:
        async for chunk in client.stream(endpoint, body, timeout):
            buffer, content, done = _stream_content(chunk, buffer)
            parts.extend(content)
            completed = completed or done
            yield chunk
    finally:
        try:
            if session_id and completed:
                await session_manager.save(session_id, original_messages + [{"role": "assistant", "content": "".join(parts)}])
        finally:
            model_manager.release_request(model_name)


async def _stream_text_completion_response(
    endpoint: str, body: dict[str, Any], timeout: float, model_name: str
):
    try:
        async for chunk in client.stream_text_completion(endpoint, body, timeout):
            yield chunk
    finally:
        model_manager.release_request(model_name)


@router.post("/chat/completions")
async def chat_completions(request: Request):
    authorize(request)
    try:
        body_obj = await request.json()
        logger.warning(
            "Incoming request:\n%s",
            json.dumps(body_obj, indent=2),
        )
        body = ChatCompletionRequest(**body_obj)
        original_messages = [msg.model_dump() for msg in body.messages]
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    # Acquire request queue semaphore before processing
    await model_manager._request_semaphore.acquire()
    await model_manager.acquire_request()
    streaming = False
    model_name: str | None = None
    try:
        session_id = request.headers.get("x-session-id")
        if session_id:
            # ``SessionManager.context`` returns a list of dicts; convert back to Message objects.
            context_msgs = await session_manager.context(session_id, original_messages)
            body.messages = [Message(**m) for m in context_msgs]
        routing_messages = [msg.model_dump() for msg in body.messages]
        if not body.model or body.model in {"auto", "gateway"}:
            await model_manager.get_endpoint(router_manager.classifier_model)
        model_name, route = await router_manager.choose_model(body.model, routing_messages)
        if route == "image_gen":
            if body.n != 1:
                raise HTTPException(status_code=400, detail="Classified image generation supports only n=1.")
            logger.info("Intent classified: image_gen")
            refiner_config = model_manager.config.get("prompt_refiner", {})
            original_prompt = next(
                (_prompt_from_message(message) for message in reversed(routing_messages) if message.get("role") == "user"),
                "",
            )
            if not original_prompt.strip():
                raise HTTPException(status_code=400, detail="Image generation requires a text prompt.")
            refined_prompt = original_prompt
            refinement_started = time.monotonic()
            try:
                if not refiner_config.get("enabled", True):
                    raise RuntimeError("Image prompt refinement is disabled.")
                refiner_model = router_manager.resolve_model(str(refiner_config.get("model", "chat")))
                logger.info("Launching chat model for image prompt refinement: %s", refiner_model)
                refiner_endpoint = await model_manager.get_endpoint(refiner_model)
                refined_prompt = await prompt_refiner.refine(
                    refiner_endpoint,
                    original_prompt,
                    model_manager.registry.get(refiner_model).timeout,
                    refiner_model,
                )
                logger.info("Prompt refinement complete in %.3fs.", time.monotonic() - refinement_started)
            except (RuntimeError, ValueError, httpx.HTTPError) as error:
                logger.warning("Prompt refinement failed after %.3fs: %s", time.monotonic() - refinement_started, error)
                if not refiner_config.get("fallback_to_original_prompt", True):
                    raise HTTPException(status_code=502, detail="Image prompt refinement failed.") from error
                logger.info("Using original prompt after refinement failure.")
            logger.info("Sending refined prompt to image backend.")
            response = await generate_image(
                refined_prompt,
                body.size,
                body.response_format,
                str(request.base_url),
            )
            logger.info("Image generation finished.")
            return response
        endpoint = await model_manager.get_endpoint(model_name)
        body.model = model_name
        timeout = model_manager.registry.get(model_name).timeout

        payload = body.model_dump(exclude_unset=True)

        logger.warning(
            "===== FORWARDED PAYLOAD =====\n%s",
            json.dumps(payload, indent=2),
        )

        logger.warning(
            "Effective sampling (%s): temp=%s, top_p=%s, max_tokens=%s",
            model_name,
            payload.get("temperature", "<server default>"),
            payload.get("top_p", "<server default>"),
            payload.get("max_tokens", "<server default>"),
        )

        if body.stream:
            streaming = True
            return StreamingResponse(
			    _stream_response(
        			endpoint,
        			payload,
        			timeout,
        			session_id,
        			original_messages,
        			model_name,
    			),
    			media_type="text/event-stream",
    			headers={"X-Model-Route": route, "Cache-Control": "no-cache"},
			)
        # Non‑streaming path
        response = await client.completion(
        	endpoint,
    		payload,
    		timeout,
		)
        if session_id:
            assistant_messages = [choice.get("message") for choice in response.get("choices", []) if choice.get("message")]
            await session_manager.save(session_id, original_messages + assistant_messages)
        return response
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except httpx.HTTPStatusError as error:
        raise HTTPException(status_code=error.response.status_code, detail=error.response.text) from error
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {error}") from error
    finally:
        if not streaming:
            model_manager.release_request(model_name)
        model_manager._request_semaphore.release()


@router.post("/completions")
async def completions(request: Request):
    """Proxy the OpenAI text-completions API used by editor autocomplete clients.

    Text completions have no chat messages to classify, so callers must select a
    configured model explicitly (for example, ``model: \"coder\"``).
    """
    authorize(request)
    try:
        body_obj = await request.json()
        logger.warning(
            "Incoming request:\n%s",
            json.dumps(body_obj, indent=2),
        )
        body = CompletionRequest(**body_obj)
        model_name = body.model
    except Exception as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    await model_manager._request_semaphore.acquire()
    await model_manager.acquire_request()
    streaming = False
    try:
        endpoint = await model_manager.get_endpoint(model_name)
        timeout = model_manager.registry.get(model_name).timeout

        payload = body.model_dump(exclude_unset=True)

        logger.warning(
            "===== FORWARDED PAYLOAD =====\n%s",
            json.dumps(payload, indent=2),
        )

        logger.warning(
            "Effective completion parameters (%s): temperature=%s, top_p=%s, max_tokens=%s",
            model_name,
            payload.get("temperature", "<server default>"),
            payload.get("top_p", "<server default>"),
            payload.get("max_tokens", "<server default>"),
        )

        if body.stream:
            streaming = True
            return StreamingResponse(
                _stream_text_completion_response(endpoint, payload, timeout, model_name),
                media_type="text/event-stream",
                headers={"X-Model-Route": "explicit", "Cache-Control": "no-cache"},
            )
        return await client.text_completion(endpoint, payload, timeout)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error
    except httpx.HTTPStatusError as error:
        raise HTTPException(status_code=error.response.status_code, detail=error.response.text) from error
    except httpx.HTTPError as error:
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {error}") from error
    finally:
        if not streaming:
            model_manager.release_request(model_name)
        model_manager._request_semaphore.release()


@router.get("/models")
async def list_models(request: Request):
    authorize(request)
    models = [
        {"id": name, "object": "model", "owned_by": "ai-gateway"}
        for name in model_manager.registry.names()
        if name != router_manager.classifier_model
    ]
    image_config = model_manager.config.get("image_generation", {})
    image_model = image_config.get("model")
    image_ids = [image_model]
    aliases = image_config.get("aliases", [])
    if isinstance(aliases, list):
        image_ids.extend(aliases)
    if image_config.get("enabled"):
        for image_id in image_ids:
            if isinstance(image_id, str) and image_id and not any(model["id"] == image_id for model in models):
                models.append({"id": image_id, "object": "model", "owned_by": "ai-gateway-image"})
    return {"object": "list", "data": models}
