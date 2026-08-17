import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from starlette.requests import Request

from api.auth import authorize
import api.images as images
from api.images import ImageResult, make_images_response
from tools.image_tools import ImageGenerator

from api.openai import _stream_content, make_chat_completion_response
from llm.prompt_refiner import ImagePromptRefiner
from managers.router_manager import RouterManager
from managers.session_manager import SessionManager
from managers.model_manager import ModelManager


class StreamingTests(unittest.TestCase):
    def test_collects_split_sse_content_and_done_marker(self):
        first = b'data: {"choices":[{"delta":{"content":"hel"}}]}\n\n'
        second = b'data: {"choices":[{"delta":{"content":"lo"}}]}\n\ndata: [DONE]\n\n'
        buffer, content, done = _stream_content(first, b"")
        self.assertEqual(content, ["hel"])
        self.assertFalse(done)
        _, content, done = _stream_content(second, buffer)
        self.assertEqual(content, ["lo"])
        self.assertTrue(done)

    def test_authorization_uses_configured_bearer_key(self):
        valid = Request({"type": "http", "headers": [(b"authorization", b"Bearer test-secret")]})
        invalid = Request({"type": "http", "headers": [(b"authorization", b"******")]})
        with patch("api.auth.model_manager.config", {"gateway": {"api_key": "test-secret"}}):
            authorize(valid)
            with self.assertRaises(HTTPException):
                authorize(invalid)


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_summary_survives_multiple_compactions(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = SessionManager(directory, max_recent_messages=2)
            await manager.save("example", [{"role": "user", "content": "first"}, {"role": "assistant", "content": "one"}, {"role": "user", "content": "second"}])
            context = await manager.context("example", [{"role": "user", "content": "third"}])
            self.assertIn("first", context[0]["content"])
            await manager.save("example", [{"role": "user", "content": "third"}, {"role": "assistant", "content": "three"}])
            saved = json.loads((Path(directory) / "example.json").read_text(encoding="utf-8"))
            self.assertIn("first", saved["summary"])


class PromptRefinerTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_external_system_prompt_and_returns_only_prompt(self):
        with tempfile.TemporaryDirectory() as directory:
            prompt_file = Path(directory) / "refiner.txt"
            prompt_file.write_text("Refine image prompts.", encoding="utf-8")
            client = AsyncMock()
            client.completion.return_value = {
                "choices": [{"message": {"content": '"A detailed sunset city"'}}]
            }
            refiner = ImagePromptRefiner({"system_prompt_file": str(prompt_file)}, client)

            result = await refiner.refine("http://127.0.0.1:8001/v1", "a city at sunset", 10, "chat-model")

            self.assertEqual(result, "A detailed sunset city")
            payload = client.completion.call_args.args[1]
            self.assertEqual(payload["messages"][0]["content"], "Refine image prompts.")
            self.assertEqual(payload["messages"][1]["content"], "a city at sunset")
            self.assertEqual(payload["model"], "chat-model")

    async def test_removes_prompt_wrappers(self):
        with tempfile.TemporaryDirectory() as directory:
            prompt_file = Path(directory) / "refiner.txt"
            prompt_file.write_text("Refine image prompts.", encoding="utf-8")
            client = AsyncMock()
            client.completion.return_value = {
                "choices": [{"message": {"content": "```text\nFinal prompt: A detailed city\n```"}}]
            }
            refiner = ImagePromptRefiner({"system_prompt_file": str(prompt_file)}, client)

            result = await refiner.refine("http://127.0.0.1:8001/v1", "a city", 10)

            self.assertEqual(result, "A detailed city")


class RouterManagerTests(unittest.TestCase):
    def test_resolves_route_aliases_to_registry_models(self):
        registry = type("Registry", (), {"get": lambda self, name: name})()
        manager = RouterManager(
            {"chat": {"model": "chat-model"}, "coder": {"model": "coder-model"}},
            registry,
            "classifier",
        )

        self.assertEqual(manager.resolve_model("chat"), "chat-model")
        self.assertEqual(manager.resolve_model("coder-model"), "coder-model")


class ImageGeneratorTests(unittest.TestCase):
    def test_rejects_invalid_size_before_invoking_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            generator = ImageGenerator({"enabled": True, "output_directory": directory, "allowed_sizes": ["square"]})
            with self.assertRaisesRegex(ValueError, "WIDTHxHEIGHT"):
                generator.prepare("a test image", "square")

    def test_rejects_disabled_generation(self):
        with tempfile.TemporaryDirectory() as directory:
            generator = ImageGenerator({"enabled": False, "output_directory": directory})
            with self.assertRaisesRegex(ValueError, "disabled"):
                generator.prepare("a test image", "1024x1024")

    def test_prepares_argument_list_and_cuda_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {name: root / name for name in ("sd-cli", "model.gguf", "vae.safetensors", "llm.gguf")}
            for path in paths.values():
                path.touch()
            generator = ImageGenerator({
                "enabled": True,
                "sd_cli": str(paths["sd-cli"]),
                "diffusion_model": str(paths["model.gguf"]),
                "vae": str(paths["vae.safetensors"]),
                "llm": str(paths["llm.gguf"]),
                "output_directory": str(root / "output"),
                "cuda_visible_devices": "0",
                "offload_to_cpu": True,
                "clip_on_cpu": True,
                "vae_on_cpu": True,
            })
            job = generator.prepare("a test image", "1024x1024")
            self.assertIn("a test image", job.command)
            self.assertEqual(job.environment["CUDA_VISIBLE_DEVICES"], "0")
            self.assertEqual(job.output_file.parent, root / "output")
            self.assertTrue({"--offload-to-cpu", "--clip-on-cpu", "--vae-on-cpu"}.issubset(job.command))

    def test_cpu_only_hides_cuda_devices(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {name: root / name for name in ("sd-cli", "model.gguf", "vae.safetensors", "llm.gguf")}
            for path in paths.values():
                path.touch()
            generator = ImageGenerator({
                "enabled": True,
                "sd_cli": str(paths["sd-cli"]),
                "diffusion_model": str(paths["model.gguf"]),
                "vae": str(paths["vae.safetensors"]),
                "llm": str(paths["llm.gguf"]),
                "output_directory": str(root / "output"),
                "cpu_only": True,
            })
            job = generator.prepare("a test image", "1024x1024")
            self.assertEqual(job.environment["CUDA_VISIBLE_DEVICES"], "")


class ImageResponseSerializationTests(unittest.TestCase):
    def test_signed_image_url_allows_fetch_without_api_key_until_expiry(self):
        with patch.object(images, "image_url_signing_secret", "test-secret"), patch.object(
            images, "image_url_ttl_seconds", 60
        ), patch("api.images.time.time", return_value=1_000):
            url = images._image_url("generated.png", "http://gateway/")
            request = Request(
                {"type": "http", "query_string": urlsplit(url).query.encode(), "headers": []}
            )

            self.assertTrue(images._has_valid_image_signature("generated.png", request))

        with patch.object(images, "image_url_signing_secret", "test-secret"), patch(
            "api.images.time.time", return_value=1_061
        ):
            self.assertFalse(images._has_valid_image_signature("generated.png", request))

    def test_images_api_keeps_b64_json_support(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "generated.png"
            image_path.write_bytes(b"image-bytes")
            image = ImageResult(1, image_path.name, "http://gateway/v1/images/generated.png", image_path)

            response = make_images_response(image, "b64_json")

            self.assertEqual(response, {"created": 1, "data": [{"b64_json": "aW1hZ2UtYnl0ZXM="}]})

    def test_chat_completion_uses_url_text_content(self):
        image = ImageResult(1, "generated.png", "http://gateway/v1/images/generated.png", Path("generated.png"))

        response = make_chat_completion_response(image, "image-model")

        message = response["choices"][0]["message"]
        self.assertEqual(response["object"], "chat.completion")
        self.assertEqual(message["role"], "assistant")
        self.assertEqual(message["content"], f"I've generated your image: {image.url}")


class ImageGenerationVRAMTests(unittest.IsolatedAsyncioTestCase):
    async def test_releases_all_gateway_owned_models_after_request_lock_is_acquired(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text(
                """models:
  persistent:
    endpoint: http://127.0.0.1:9000/v1
    persistent: true
  chat:
    endpoint: http://127.0.0.1:8001/v1
""",
                encoding="utf-8",
            )
            manager = ModelManager(config_path)
            manager._processes = {"persistent": object(), "chat": object()}
            await manager.acquire_request()
            try:
                with patch.object(manager, "unload_model", new=AsyncMock()) as unload_model:
                    await manager.release_models_for_image_generation()
                self.assertCountEqual(
                    [call.args[0] for call in unload_model.await_args_list],
                    ["persistent", "chat"],
                )
            finally:
                manager.release_request()

    async def test_refuses_to_release_models_without_exclusive_request_access(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.yaml"
            config_path.write_text("models: {}\n", encoding="utf-8")
            manager = ModelManager(config_path)
            with self.assertRaisesRegex(RuntimeError, "acquire the request lock"):
                await manager.release_models_for_image_generation()


if __name__ == "__main__":
    unittest.main()
