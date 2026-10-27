"""Agent loop on the OpenAI Responses API with native function calling (no framework)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass

from dotenv import load_dotenv
from openai import OpenAI

from emporio.prompt import build_system_prompt
from emporio.tools import DATA_DIR, run_tool, tool_schemas

DEFAULT_MODEL = "gpt-6-luna"
DEFAULT_REASONING_EFFORT = "low"
MAX_TOOL_CALLS = 5  # per customer message


@dataclass
class Usage:
    model_calls: int = 0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0

    def add(self, usage) -> None:
        if usage is None:
            return
        self.model_calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        details = getattr(usage, "input_tokens_details", None)
        self.cached_tokens += getattr(details, "cached_tokens", 0) or 0

    def __str__(self) -> str:
        return (
            f"model calls: {self.model_calls} | input tokens: {self.input_tokens:,} "
            f"(cached: {self.cached_tokens:,}) | output tokens: {self.output_tokens:,}"
        )


class Agent:
    """One customer conversation. History lives in memory for the session only."""

    def __init__(self, model: str | None = None, client: OpenAI | None = None):
        load_dotenv(DATA_DIR.parent / ".env")
        self.model = model or os.getenv("MODEL") or DEFAULT_MODEL
        self.reasoning_effort = os.getenv("REASONING_EFFORT", DEFAULT_REASONING_EFFORT).strip().lower()
        self.client = client or OpenAI()
        self.instructions = build_system_prompt()
        self.tools = tool_schemas()
        self.history: list = []
        self.usage = Usage()
        self.trace: list[dict] = []  # tool calls made while answering the last message

    def _create(self, tool_choice: str):
        options = {}
        if self.reasoning_effort not in ("", "off"):  # set REASONING_EFFORT=off for non-reasoning models
            # store=False keeps customer data off OpenAI's side, so reasoning comes back encrypted.
            options = {"reasoning": {"effort": self.reasoning_effort}, "include": ["reasoning.encrypted_content"]}
        response = self.client.responses.create(
            model=self.model,
            instructions=self.instructions,
            input=self.history,
            tools=self.tools,
            tool_choice=tool_choice,
            store=False,
            prompt_cache_key="emporio-agent",
            **options,
        )
        self.usage.add(response.usage)
        return response

    def reply(self, text: str) -> str:
        self.history.append({"role": "user", "content": text})
        self.trace = []
        calls = 0
        while True:
            # After the limit the model must answer with what it already has.
            response = self._create("auto" if calls < MAX_TOOL_CALLS else "none")
            self.history += response.output
            function_calls = [item for item in response.output if item.type == "function_call"]
            if not function_calls:
                return response.output_text.strip() or "Desculpe, não consegui responder agora. Pode repetir?"
            for call in function_calls:
                if calls < MAX_TOOL_CALLS:
                    result = run_tool(call.name, call.arguments)
                    calls += 1
                else:
                    result = {"error": f"Limite de {MAX_TOOL_CALLS} consultas por mensagem atingido."}
                self.trace.append({"tool": call.name, "arguments": call.arguments, "result": result})
                self.history.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result, ensure_ascii=False),
                    }
                )
