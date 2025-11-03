from __future__ import annotations

import json
from typing import Any, Dict, List

import httpx
import yaml

from .config import settings
from .schemas import StrategyComponent, StrategyPlan


STRATEGY_TEMPLATE = """# Unified Strategy YAML Schema
name: <Concise strategy name>
description: <Short natural language description>
entry:
  - rule: sma_crossover | ema_crossover | rsi_oversold | price_breakout
    params:
      fast_period: <int>        # for crossover rules
      slow_period: <int>        # for crossover rules
      rsi_period: <int>         # for rsi_oversold
      rsi_threshold: <int>      # for rsi_oversold, default 30
      breakout_period: <int>    # for price_breakout
      order_pct: <float 0-1>    # fraction of cash to allocate per order
add_position:
  - rule: optional, same rule set as entry
    params:
      [...]
reduce_position:
  - rule: optional, same rule set as entry
    params:
      [...]
take_profit:
  - rule: percent_target | trailing_stop
    params:
      percent: <float>          # e.g. 0.1 for 10%
risk_management:
  stop_loss:
    rule: percent_floor | atr_stop
    params:
      percent: <float>
      atr_period: <int>
      atr_multiplier: <float>
  max_drawdown: <float 0-1>
  risk_per_trade: <float 0-1>
exit:
  - rule: time_stop | trailing_stop
    params:
      days: <int>               # for time_stop
      percent: <float>          # for trailing_stop
portfolio:
  max_positions: <int>
  commission: <float per trade>
notes:
  - <Any useful reminder for the quant team>
"""


class LLMClientError(RuntimeError):
    """Raised when LLM interaction fails."""


class LLMClient:
    """Thin wrapper around the DeepSeek chat-completions API."""

    def __init__(self, api_key: str | None = None, api_base: str | None = None) -> None:
        self.api_key = api_key or settings.deepseek_api_key
        self.api_base = api_base or settings.deepseek_api_base
        if not self.api_key:
            raise LLMClientError(
                "Missing DeepSeek API key. Define DEEPSEEK_API_KEY in your environment."
            )
        self._client = httpx.Client(timeout=httpx.Timeout(60.0, read=120.0))

    def _post_chat(self, messages: List[Dict[str, str]], response_format: str | None = None) -> str:
        url = f"{self.api_base.rstrip('/')}/chat/completions"
        payload: Dict[str, Any] = {"model": "deepseek-chat", "messages": messages}
        if response_format:
            payload["response_format"] = {"type": response_format}

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            response = self._client.post(url, headers=headers, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise LLMClientError(f"DeepSeek API call failed: {exc}") from exc

        data = response.json()
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError) as exc:
            raise LLMClientError(f"Unexpected response payload: {data}") from exc

    def generate_strategy_plan(self, user_prompt: str) -> StrategyPlan:
        """Generate structured plan, YAML config, and text diagram for the strategy."""
        system_prompt = (
            "You are a quantitative trading assistant. Parse the user's request and produce a JSON "
            "object with keys 'components', 'yaml', 'diagram', and 'reasoning'. "
            "'components' must be a list of objects with fields "
            "category, objective, and rules. "
            "Use only the following module categories when applicable: "
            "entry, add_position, reduce_position, take_profit, stop_loss, risk_management, "
            "portfolio, exit. "
            "The 'yaml' field MUST follow the provided unified schema exactly, filling concrete "
            "values that are internally consistent. "
            "Ensure indicators reference supported rule names. "
            "The 'diagram' should be a concise ASCII flow summary with arrows and modules. "
            "Respond with compact JSON, no Markdown."
        )

        prompt = (
            f"User request:\n{user_prompt}\n\n"
            f"Unified YAML schema:\n{STRATEGY_TEMPLATE}\n\n"
            "Produce thoughtful, realistic parameters aligned with the user's intent."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ]
        raw = self._post_chat(messages, response_format=None)
        sanitized = self._sanitize_json_block(raw)
        try:
            plan_payload = json.loads(sanitized)
        except json.JSONDecodeError as exc:
            raise LLMClientError(f"Failed to parse strategy plan JSON: {raw}") from exc

        components = []
        for item in plan_payload.get("components", []):
            raw_rules = item.get("rules", [])
            if isinstance(raw_rules, str):
                rules = [raw_rules]
            elif isinstance(raw_rules, list):
                rules = [str(rule) for rule in raw_rules]
            else:
                rules = [str(raw_rules)]
            components.append(
                StrategyComponent(
                    category=item.get("category", ""),
                    objective=item.get("objective", ""),
                    rules=rules,
                )
            )

        yaml_content = plan_payload.get("yaml", "")
        if isinstance(yaml_content, dict):
            yaml_content = yaml.safe_dump(yaml_content, sort_keys=False, allow_unicode=False)

        return StrategyPlan(
            components=components,
            yaml=yaml_content,
            diagram=plan_payload.get("diagram", ""),
            reasoning=plan_payload.get("reasoning"),
        )

    def interpret_backtest(self, plan_summary: str, metrics: Dict[str, Any]) -> str:
        """Ask the LLM to interpret backtest metrics and suggest improvements."""
        system_prompt = (
            "You are a quantitative research mentor. Interpret backtest metrics, highlight "
            "strengths, weaknesses, and propose 2-3 concrete improvements."
        )
        metrics_text = json.dumps(metrics, indent=2)
        user_prompt = (
            f"Strategy plan summary:\n{plan_summary}\n\n"
            f"Backtest metrics:\n{metrics_text}\n\n"
            "Write a short analysis (less than 200 words) with bullet suggestions."
        )
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return self._post_chat(messages)

    @staticmethod
    def _sanitize_json_block(raw: str) -> str:
        """Remove Markdown fences or commentary around JSON before parsing."""
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            # drop opening fence
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            # drop closing fence
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end >= start:
            text = text[start : end + 1]
        return text
