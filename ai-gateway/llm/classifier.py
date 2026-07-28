from __future__ import annotations

import json
from typing import Any

import httpx


class LLMClassifier:
    """Classifies an incoming conversation into one configured route."""

    def __init__(self, routes: dict[str, Any], model_name: str, timeout: float = 20):
        self.routes = routes
        self.model_name = model_name
        self.timeout = timeout

    @property
    def route_names(self) -> list[str]:
        return list(self.routes)

    def fallback(self, messages: list[dict[str, Any]]) -> str:
        text = " ".join(str(item.get("content", "")) for item in messages if item.get("role") == "user").lower()
        for route_name, route in self.routes.items():
            if any(keyword.lower() in text for keyword in route.get("keywords", [])):
                return route_name
        return "chat" if "chat" in self.routes else self.route_names[0]

    async def classify(self, endpoint: str, messages: list[dict[str, Any]]) -> str:
        routes = json.dumps({name: {"model": config.get("model"), "keywords": config.get("keywords", [])} for name, config in self.routes.items()})
        conversation = json.dumps(messages[-8:], ensure_ascii=False)
        # This prompt must be changed when new routes are added.
        prompt = f"""
        You must classify the user's primary intent into exactly one route.

        Available routes:

            chat
            - General conversation
            - Questions and answers
            - Brainstorming
            - Summarization
            - Translation
            - Writing
            - Light research
            - Reasoning
            - Mathematics
            - Explaining concepts
            - Planning
            - writing
            - emails
            - summaries
            - research
            - brainstorming
            - mathematics
            - planning
            - legal
            - finance
            - health
            - history
            - recipes
            - travel
            - Any request that is NOT primarily coding or image generation

            coder
            - Writing code
            - Explaining code
            - Debugging
            - Fixing errors
            - Programming questions
            - APIs
            - SQL
            - Bash
            - PowerShell
            - Docker
            - Git
            - Linux commands
            - Kubernetes
            - Infrastructure as Code
            - Regex
            - Refactoring
            - Code review
            - Software architecture
            - Programming documentation
            - Anything where source code, configuration, or software engineering is the primary task
            - programming
            - scripts
            - shell
            - SQL
            - regex
            - API
            - JSON schema
            - YAML
            - Docker
            - Git
            - Kubernetes
            - CI/CD
            - debugging
            - stack traces
            - compiler errors
            - software architecture
            - code review
            - explain code
            - optimize code
            - c-coding
            - ESP-IDF
            - Cmake
            - cpp-coding
            - C Sharp
            - codebases
            - MQTT
            - LoRA
            - Python

            image_gen
            - Generate an image
            - Draw
            - Create artwork
            - Make a logo
            - Design an icon
            - Create a poster
            - Produce an illustration
            - Generate a photorealistic image
            - Edit an existing image
            - Remove background
            - Upscale
            - Inpaint
            - Outpaint
            - Style transfer
            - Any request whose primary output is an image
            - create image
            - generate image
            - draw
            - paint
            - render
            - illustration
            - logo
            - icon
            - banner
            - wallpaper
            - remove background
            - edit photo
            - upscale
            - inpaint
            - outpaint
            - Ghibli style
            - cartoonize

        Examples:

            User: How do I reverse a linked list in C++?
            Route: coder

            User: Fix this Python traceback.
            Route: coder

            User: Explain Docker volumes.
            Route: coder

            User: Draw a futuristic cyberpunk city.
            Route: image_gen

            User: Remove the background from this photo.
            Route: image_gen

            User: Turn this sketch into anime.
            Route: image_gen

            User: Tell me about quantum computing.
            Route: chat

            User: Summarize this article.
            Route: chat

            User: Translate this email into Japanese.
            Route: chat

            User: Help me plan a vacation.
            Route: chat

            User: Help me look for this product's information.
            Route: chat

        Negative Examples:

            "Write a README" -> chat
            "Explain this code" -> coder
            "Generate Markdown documentation from this code" -> coder
            "Create a UML diagram" -> chat
            "Draw a UML diagram as an image" -> image_gen
            "How does Git work?" -> coder
            "Who created Git?" -> chat
            "Make a PowerPoint" -> chat
            "Create an icon for my app" -> image_gen

        Decision process:

            A. Is the user requesting creation or modification of an image?
            → image_gen

            Else:

            B. Is the user's primary task software engineering, programming, scripting, debugging, configuration, or code explanation?
            → coder

            Else:
            → chat

        Classification rules:

            1. Choose image_gen whenever the user is asking for an image to be created or modified.
            2. Choose coder whenever the primary task is software development.
            3. Otherwise choose chat.
            4. If multiple topics are present, classify according to the PRIMARY requested output.
            5. If uncertain, choose chat.

        Valid outputs:
            {routes}

        Conversation:
            {conversation}
        """
        payload = {
            "model": self.model_name,
            "messages": [{"role": "system", "content": "Return only a valid route name."}, {"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 16,
            "stream": False,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(f"{endpoint}/chat/completions", json=payload)
                response.raise_for_status()
            route = response.json()["choices"][0]["message"]["content"].strip().strip("`\"'").lower()
            if route in self.routes:
                return route
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
            pass
        return self.fallback(messages)
