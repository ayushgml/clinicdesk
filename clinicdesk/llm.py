"""The sole network/LLM boundary, with provider-neutral text and usage."""
import os
from dataclasses import dataclass

# USD per million tokens, OpenRouter catalog snapshot 2026-10-11.
PRICES = {
    "z-ai/glm-5.3": (0.039, 4.8),
    "qwen/qwen3.8-flash": (0.15, 0.47),
    "meta-llama/llama-3.1-8b-instruct": (0.05, 0.08),
    "google/gemini-3.8-flash": (0.75, 3.75),
    "claude-sonnet-4-6": (3.0, 15.0),
}

@dataclass
class Reply:
    text: str
    input_tokens: int
    output_tokens: int
    cost: float | None

class LLM:
    max_output_tokens = 1500

    def __init__(self):
        self.provider = os.getenv("LLM_PROVIDER") or ("anthropic" if os.getenv("ANTHROPIC_API_KEY") else "openrouter")
        if self.provider not in ("anthropic", "openrouter"):
            raise ValueError("LLM_PROVIDER must be anthropic or openrouter")
        self.model = os.getenv("VICTIM_MODEL") or ("claude-sonnet-4-6" if self.provider == "anthropic" else "z-ai/glm-5.3")
        key_name = "ANTHROPIC_API_KEY" if self.provider == "anthropic" else "OPENROUTER_API_KEY"
        if not os.getenv(key_name):
            raise ValueError(f"Missing {key_name}")
        self.prices = PRICES.get(self.model)
        pin, pout = os.getenv("VICTIM_PRICE_IN"), os.getenv("VICTIM_PRICE_OUT")
        if pin is not None or pout is not None:
            if pin is None or pout is None:
                raise ValueError("Set both VICTIM_PRICE_IN and VICTIM_PRICE_OUT")
            self.prices = (float(pin), float(pout))
            if any(p < 0 for p in self.prices):
                raise ValueError("Prices must be nonnegative")
        if self.provider == "anthropic":
            from anthropic import Anthropic
            self.client = Anthropic(api_key=os.environ[key_name], max_retries=0, timeout=45)
        else:
            from openai import OpenAI
            self.client = OpenAI(api_key=os.environ[key_name], base_url="https://openrouter.ai/api/v1", max_retries=0, timeout=45)
        self.input_tokens = 0
        self.output_tokens = 0
        self.cost = 0.0
        self.cost_known = True
        self.calls = []
        self.before_call = None

    def complete(self, system: str, messages: list[dict]) -> Reply:
        params = dict(model=self.model, max_tokens=self.max_output_tokens, temperature=0)
        if self.provider == "anthropic":
            params.update(system=system, messages=messages)
            create = self.client.messages.create
        else:
            params.update(messages=[{"role": "system", "content": system}, *messages],
                          extra_body={"reasoning": {"max_tokens": 128}})
            create = self.client.chat.completions.create
        if self.before_call:
            self.before_call(system, messages, self.max_output_tokens)
        for attempt in range(2):
            try:
                response = create(**params)
                break
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                if attempt == 0 and status in (400, 422) and "temperature" in str(exc).lower():
                    params.pop("temperature", None)
                    continue
                # A transport failure may have been billed; do not invent zero cost.
                if status is None or status >= 500:
                    self.cost_known = False
                raise
        if self.provider == "anthropic":
            text = "".join(b.text for b in response.content if b.type == "text")
            incoming, outgoing = response.usage.input_tokens, response.usage.output_tokens
            reported_cost = None
        else:
            text = response.choices[0].message.content or ""
            if response.usage is None:
                self.cost_known = False
                raise RuntimeError("Provider omitted usage")
            incoming, outgoing = response.usage.prompt_tokens, response.usage.completion_tokens
            reported_cost = getattr(response.usage, "cost", None)
        cost = float(reported_cost) if reported_cost is not None else (
            (incoming * self.prices[0] + outgoing * self.prices[1]) / 1_000_000 if self.prices else None
        )
        self.input_tokens += incoming
        self.output_tokens += outgoing
        if cost is None:
            self.cost_known = False
        else:
            self.cost += cost
        self.calls.append({"model": self.model, "provider": self.provider, "input": incoming,
                           "output": outgoing, "cost": cost, "response_id": response.id})
        return Reply(text, incoming, outgoing, cost)
