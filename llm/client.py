import asyncio
import json
import logging
import re

import httpx

from ..config import MimirConfig

log = logging.getLogger(__name__)


def parse_json_response(text: str) -> dict | list | None:
    """Best-effort JSON extraction from LLM output."""
    text = text.strip()
    # Direct parse
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Markdown code block
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    # Find outermost braces/brackets
    for start_ch, end_ch in [("{", "}"), ("[", "]")]:
        i = text.find(start_ch)
        j = text.rfind(end_ch)
        if i >= 0 and j > i:
            try:
                return json.loads(text[i : j + 1])
            except json.JSONDecodeError:
                pass
    return None


# DeepSeek pricing per million tokens
_PRICING = {
    "deepseek-chat": {"input": 0.27, "output": 1.10},
    "deepseek-reasoner": {"input": 0.55, "output": 2.19},
}


class LLMClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        max_tokens: int = 2000,
        temperature: float = 0.3,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.default_max_tokens = max_tokens
        self.default_temperature = temperature
        self._total_prompt_tokens = 0
        self._total_completion_tokens = 0
        self._total_cost = 0.0
        self._call_count = 0
        self._hourly_timestamps: list[float] = []
        self.max_calls_per_hour = 999999  # no self-imposed limit
        # Per-call instrumentation
        self._call_log: list[dict] = []
        self._max_call_log = 2000

    def _check_hourly_limit(self) -> None:
        """Enforce max LLM calls per hour."""
        import time as _time
        now = _time.time()
        cutoff = now - 3600
        self._hourly_timestamps = [t for t in self._hourly_timestamps if t > cutoff]
        if len(self._hourly_timestamps) >= self.max_calls_per_hour:
            raise RuntimeError(
                f"LLM rate limit: {self.max_calls_per_hour} calls/hour exceeded "
                f"({len(self._hourly_timestamps)} in last hour)"
            )
        self._hourly_timestamps.append(now)

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float | None = None,
        max_tokens: int | None = None,
        caller: str = "unknown",
    ) -> str:
        """Send chat completion request with retry.

        caller: identifies who called this (e.g. 'intent_to_query', 'extract_beliefs',
        'chat_answer', 'reason', 'classify_intent', 'dedup', etc.)
        """
        self._check_hourly_limit()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens or self.default_max_tokens,
            "temperature": temperature if temperature is not None else self.default_temperature,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        last_err: Exception | None = None
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.post(
                        f"{self.base_url}/v1/chat/completions",
                        json=payload,
                        headers=headers,
                    )
                    resp.raise_for_status()
                    data = resp.json()

                # Track usage
                usage = data.get("usage", {})
                pt = usage.get("prompt_tokens", 0)
                ct = usage.get("completion_tokens", 0)
                self._total_prompt_tokens += pt
                self._total_completion_tokens += ct
                pricing = _PRICING.get(self.model, {"input": 0.27, "output": 1.10})
                self._total_cost += (pt * pricing["input"] + ct * pricing["output"]) / 1_000_000
                self._call_count += 1

                # Per-call log
                import time as _t
                self._call_log.append({
                    "caller": caller,
                    "prompt_tokens": pt,
                    "completion_tokens": ct,
                    "prompt_len": len(system_prompt) + len(user_prompt),
                    "system_len": len(system_prompt),
                    "user_len": len(user_prompt),
                    "timestamp": _t.time(),
                })
                if len(self._call_log) > self._max_call_log:
                    self._call_log = self._call_log[-self._max_call_log:]

                content = data["choices"][0]["message"]["content"]
                return content or ""

            except Exception as e:
                last_err = e
                wait = 2 ** attempt
                log.warning("LLM call attempt %d failed: %s, retrying in %ds", attempt + 1, e, wait)
                await asyncio.sleep(wait)

        raise RuntimeError(f"LLM call failed after 3 attempts: {last_err}")

    def get_usage_stats(self) -> dict:
        return {
            "call_count": self._call_count,
            "prompt_tokens": self._total_prompt_tokens,
            "completion_tokens": self._total_completion_tokens,
            "estimated_cost_usd": round(self._total_cost, 6),
        }

    def get_call_log(self, last_n: int = 100) -> list[dict]:
        """Return last N per-call records."""
        return self._call_log[-last_n:]

    def get_caller_stats(self) -> dict:
        """Aggregate stats by caller name."""
        stats: dict[str, dict] = {}
        for entry in self._call_log:
            c = entry["caller"]
            if c not in stats:
                stats[c] = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_prompt_chars": 0}
            stats[c]["calls"] += 1
            stats[c]["prompt_tokens"] += entry["prompt_tokens"]
            stats[c]["completion_tokens"] += entry["completion_tokens"]
            stats[c]["total_prompt_chars"] += entry["prompt_len"]
        # Add averages
        for c, s in stats.items():
            s["avg_prompt_tokens"] = s["prompt_tokens"] // max(s["calls"], 1)
            s["avg_prompt_chars"] = s["total_prompt_chars"] // max(s["calls"], 1)
        return stats
