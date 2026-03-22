"""
Anthropic API client for Layer 3 analyst LLM calls.

Two analysis paths:
  analyze_single() - single market, uses submit_analysis tool
  analyze_event()  - co-event group, uses submit_event_analysis tool
                     which returns {"analyses": [per-token analysis, ...]}

Schema validation: if the model does not call the expected tool, or if the
tool input fails validation, a ValueError is raised. The caller must catch
this and persist INVALID_OUTPUT.

Cost model (approximate, update if Anthropic pricing changes):
  Model                       Input $/1M   Output $/1M
  claude-haiku-4-5-*          0.80         4.00
  claude-sonnet-4-5-*         3.00        15.00
  claude-sonnet-4-6            3.00        15.00
  claude-opus-4-6              15.00       75.00
"""
from __future__ import annotations

import logging

import httpx

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Token pricing table (USD per million tokens)
# ---------------------------------------------------------------------------

_PRICE_TABLE: list[tuple[str, float, float]] = [
    ("claude-haiku-4-5", 0.80, 4.00),
    ("claude-haiku", 0.80, 4.00),
    ("claude-sonnet-4-6", 3.00, 15.00),
    ("claude-sonnet-4-5", 3.00, 15.00),
    ("claude-sonnet", 3.00, 15.00),
    ("claude-opus-4-6", 15.00, 75.00),
    ("claude-opus", 15.00, 75.00),
]
_FALLBACK_PRICE = (3.00, 15.00)


def token_cost_usd(
    model: str, input_tokens: int, output_tokens: int
) -> tuple[float, float, float]:
    """
    Returns (total_usd, input_usd, output_usd) for the given token counts.
    Uses longest-prefix matching against _PRICE_TABLE.
    """
    model_lower = model.lower()
    price_in, price_out = _FALLBACK_PRICE
    for prefix, p_in, p_out in _PRICE_TABLE:
        if model_lower.startswith(prefix):
            price_in, price_out = p_in, p_out
            break
    input_usd = input_tokens * price_in / 1_000_000
    output_usd = output_tokens * price_out / 1_000_000
    return input_usd + output_usd, input_usd, output_usd


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

_SINGLE_ANALYSIS_PROPERTIES = {
    "fair_probability": {
        "type": "number",
        "minimum": 0.0,
        "maximum": 1.0,
        "description": "Estimated probability that the market resolves YES.",
    },
    "confidence": {
        "type": "number",
        "minimum": 0.0,
        "maximum": 1.0,
        "description": "Your confidence in the estimate (0=no basis, 1=very certain).",
    },
    "evidence_summary": {
        "type": "string",
        "maxLength": 500,
        "description": "1-3 sentence summary of the key evidence used.",
    },
    "reasoning": {
        "type": "string",
        "description": "Full reasoning chain leading to the probability estimate.",
    },
    "is_ambiguous": {
        "type": "boolean",
        "description": "True if the market question or resolution criteria are unclear.",
    },
}

SUBMIT_ANALYSIS_TOOL: dict = {
    "name": "submit_analysis",
    "description": (
        "Submit your probability analysis for a single prediction market. "
        "Called exactly once per analysis request."
    ),
    "input_schema": {
        "type": "object",
        "required": [
            "fair_probability",
            "confidence",
            "evidence_summary",
            "reasoning",
            "is_ambiguous",
        ],
        "properties": _SINGLE_ANALYSIS_PROPERTIES,
    },
}

# Per-token schema used inside submit_event_analysis
_EVENT_TOKEN_SCHEMA: dict = {
    "type": "object",
    "required": [
        "token_id",
        "fair_probability",
        "confidence",
        "evidence_summary",
        "reasoning",
        "is_ambiguous",
    ],
    "properties": {
        "token_id": {"type": "string"},
        **_SINGLE_ANALYSIS_PROPERTIES,
    },
}

SUBMIT_EVENT_ANALYSIS_TOOL: dict = {
    "name": "submit_event_analysis",
    "description": (
        "Submit probability analyses for all markets in a co-event group. "
        "The analyses list must contain one entry per market token listed in the prompt."
    ),
    "input_schema": {
        "type": "object",
        "required": ["analyses"],
        "properties": {
            "analyses": {
                "type": "array",
                "items": _EVENT_TOKEN_SCHEMA,
                "description": "One analysis object per market token.",
            }
        },
    },
}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AnthropicAnalystClient:
    """
    Async HTTP client wrapping Anthropic Messages API for structured analysis.

    All calls use tool_choice to force exactly one tool response.
    On schema failure, raises ValueError("INVALID_OUTPUT: ...").
    """

    def __init__(self, api_key: str, timeout_seconds: float = 60.0) -> None:
        self._api_key = api_key
        self._client = httpx.AsyncClient(
            base_url="https://api.anthropic.com",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            timeout=timeout_seconds,
        )

    async def analyze_single(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
    ) -> tuple[dict, int, int]:
        """
        Analyse a single market.

        Returns (tool_input_dict, input_tokens, output_tokens).
        Raises ValueError if tool is not called or input is invalid.
        """
        payload = self._build_payload(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tool=SUBMIT_ANALYSIS_TOOL,
        )
        data = await self._call(payload)
        tool_input = _extract_tool_input(data, "submit_analysis")
        _validate_single_analysis(tool_input)
        usage = data.get("usage", {})
        return tool_input, usage.get("input_tokens", 0), usage.get("output_tokens", 0)

    async def analyze_event(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        expected_token_ids: list[str],
    ) -> tuple[list[dict], int, int]:
        """
        Analyse a co-event group.

        Returns (list_of_per_token_dicts, input_tokens, output_tokens).
        Each dict contains the same keys as a single analysis plus token_id.
        Raises ValueError if tool is not called or output is malformed.
        """
        payload = self._build_payload(
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tool=SUBMIT_EVENT_ANALYSIS_TOOL,
        )
        data = await self._call(payload)
        tool_input = _extract_tool_input(data, "submit_event_analysis")
        analyses = _validate_event_analysis(tool_input, expected_token_ids)
        usage = data.get("usage", {})
        return analyses, usage.get("input_tokens", 0), usage.get("output_tokens", 0)

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------

    def _build_payload(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        tool: dict,
    ) -> dict:
        return {
            "model": model,
            "max_tokens": 2048,
            "system": system_prompt,
            "tools": [tool],
            "tool_choice": {"type": "tool", "name": tool["name"]},
            "messages": [{"role": "user", "content": user_prompt}],
        }

    async def _call(self, payload: dict) -> dict:
        try:
            resp = await self._client.post("/v1/messages", json=payload)
            if resp.status_code == 429:
                raise ValueError("RATE_LIMITED")
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as exc:
            raise ValueError(f"HTTP {exc.response.status_code}") from exc


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def _extract_tool_input(data: dict, expected_tool_name: str) -> dict:
    """Find the tool_use block in the response and return its input dict."""
    for block in data.get("content", []):
        if (
            block.get("type") == "tool_use"
            and block.get("name") == expected_tool_name
        ):
            return block.get("input", {})
    raise ValueError(
        f"INVALID_OUTPUT: model did not call {expected_tool_name!r}. "
        f"stop_reason={data.get('stop_reason')!r}"
    )


def _validate_single_analysis(inp: dict) -> None:
    """Raise ValueError if required fields are missing or out of range."""
    for key in ("fair_probability", "confidence", "evidence_summary", "is_ambiguous"):
        if key not in inp:
            raise ValueError(f"INVALID_OUTPUT: missing field {key!r}")
    p = inp["fair_probability"]
    c = inp["confidence"]
    if not (0.0 <= p <= 1.0):
        raise ValueError(f"INVALID_OUTPUT: fair_probability={p!r} out of [0,1]")
    if not (0.0 <= c <= 1.0):
        raise ValueError(f"INVALID_OUTPUT: confidence={c!r} out of [0,1]")


def _validate_event_analysis(inp: dict, expected_token_ids: list[str]) -> list[dict]:
    """Validate the event analysis and return the per-token list."""
    analyses = inp.get("analyses")
    if not isinstance(analyses, list) or not analyses:
        raise ValueError("INVALID_OUTPUT: 'analyses' missing or empty")
    for item in analyses:
        if "token_id" not in item:
            raise ValueError("INVALID_OUTPUT: analysis item missing 'token_id'")
        _validate_single_analysis(item)
    # Build lookup by token_id; return in expected order, filling unknowns
    by_tid = {a["token_id"]: a for a in analyses}
    result = []
    for tid in expected_token_ids:
        if tid in by_tid:
            result.append(by_tid[tid])
        else:
            log.warning(
                "Event analysis missing token_id %s; using placeholder", tid[:20]
            )
            result.append(
                {
                    "token_id": tid,
                    "fair_probability": 0.5,
                    "confidence": 0.0,
                    "evidence_summary": "Not returned by model.",
                    "reasoning": "",
                    "is_ambiguous": True,
                }
            )
    return result
