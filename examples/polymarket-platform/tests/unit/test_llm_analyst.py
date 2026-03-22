"""Tests for polymarket_platform/analyst/llm.py."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from polymarket_platform.analyst.llm import (
    AnthropicAnalystClient,
    _extract_tool_input,
    _validate_event_analysis,
    _validate_single_analysis,
    token_cost_usd,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_mock_response(content_blocks, input_tokens=100, output_tokens=50, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = {
        "content": content_blocks,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
        "stop_reason": "tool_use",
    }
    resp.raise_for_status.return_value = None
    return resp


def make_tool_use_block(name: str, input_dict: dict) -> dict:
    return {"type": "tool_use", "name": name, "input": input_dict}


def make_valid_analysis_input(**overrides) -> dict:
    base = {
        "fair_probability": 0.62,
        "confidence": 0.7,
        "evidence_summary": "Key evidence here.",
        "reasoning": "Full reasoning chain.",
        "is_ambiguous": False,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# token_cost_usd
# ---------------------------------------------------------------------------


def test_token_cost_usd_sonnet():
    total, input_usd, output_usd = token_cost_usd("claude-sonnet-4-6", 1000, 500)
    # Input: 1000 * 3.00 / 1_000_000 = 0.003
    # Output: 500 * 15.00 / 1_000_000 = 0.0075
    assert pytest.approx(input_usd, rel=1e-6) == 0.003
    assert pytest.approx(output_usd, rel=1e-6) == 0.0075
    assert pytest.approx(total, rel=1e-6) == 0.003 + 0.0075


def test_token_cost_usd_haiku():
    total, input_usd, output_usd = token_cost_usd("claude-haiku-4-5", 1_000_000, 1_000_000)
    # Input: 1M * 0.80 / 1M = 0.80
    # Output: 1M * 4.00 / 1M = 4.00
    assert pytest.approx(input_usd, rel=1e-6) == 0.80
    assert pytest.approx(output_usd, rel=1e-6) == 4.00
    assert pytest.approx(total, rel=1e-6) == 4.80


def test_token_cost_usd_unknown_model():
    # Falls back to sonnet pricing (3.00 / 15.00)
    total, input_usd, output_usd = token_cost_usd("claude-unknown-model", 1000, 500)
    assert pytest.approx(input_usd, rel=1e-6) == 0.003
    assert pytest.approx(output_usd, rel=1e-6) == 0.0075
    assert pytest.approx(total, rel=1e-6) == 0.003 + 0.0075


# ---------------------------------------------------------------------------
# _extract_tool_input
# ---------------------------------------------------------------------------


def test_extract_tool_input_found():
    data = {
        "content": [
            {"type": "text", "text": "Thinking..."},
            make_tool_use_block("submit_analysis", {"fair_probability": 0.6, "confidence": 0.8}),
        ],
        "stop_reason": "tool_use",
    }
    result = _extract_tool_input(data, "submit_analysis")
    assert result == {"fair_probability": 0.6, "confidence": 0.8}


def test_extract_tool_input_missing_raises():
    data = {
        "content": [{"type": "text", "text": "I cannot provide an analysis."}],
        "stop_reason": "end_turn",
    }
    with pytest.raises(ValueError, match="INVALID_OUTPUT"):
        _extract_tool_input(data, "submit_analysis")


# ---------------------------------------------------------------------------
# _validate_single_analysis
# ---------------------------------------------------------------------------


def test_validate_single_analysis_valid():
    inp = make_valid_analysis_input()
    # Should not raise
    _validate_single_analysis(inp)


def test_validate_single_analysis_missing_field():
    inp = make_valid_analysis_input()
    del inp["confidence"]
    with pytest.raises(ValueError, match="confidence"):
        _validate_single_analysis(inp)


def test_validate_single_analysis_probability_out_of_range():
    inp = make_valid_analysis_input(fair_probability=1.5)
    with pytest.raises(ValueError, match="fair_probability"):
        _validate_single_analysis(inp)


# ---------------------------------------------------------------------------
# _validate_event_analysis
# ---------------------------------------------------------------------------


def test_validate_event_analysis_returns_in_order():
    analyses = [
        {**make_valid_analysis_input(), "token_id": "tok-B"},
        {**make_valid_analysis_input(), "token_id": "tok-A"},
    ]
    inp = {"analyses": analyses}
    expected_token_ids = ["tok-A", "tok-B"]
    result = _validate_event_analysis(inp, expected_token_ids)
    assert len(result) == 2
    assert result[0]["token_id"] == "tok-A"
    assert result[1]["token_id"] == "tok-B"


def test_validate_event_analysis_missing_token_fills_placeholder():
    analyses = [
        {**make_valid_analysis_input(), "token_id": "tok-A"},
    ]
    inp = {"analyses": analyses}
    expected_token_ids = ["tok-A", "tok-B"]
    result = _validate_event_analysis(inp, expected_token_ids)
    assert len(result) == 2
    placeholder = result[1]
    assert placeholder["token_id"] == "tok-B"
    assert placeholder["confidence"] == 0.0
    assert placeholder["is_ambiguous"] is True


# ---------------------------------------------------------------------------
# AnthropicAnalystClient — analyze_single
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_single_success():
    valid_input = make_valid_analysis_input()
    content_blocks = [make_tool_use_block("submit_analysis", valid_input)]
    mock_resp = make_mock_response(content_blocks, input_tokens=120, output_tokens=80)

    client = AnthropicAnalystClient(api_key="test-key")
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        result, input_tokens, output_tokens = await client.analyze_single(
            model="claude-sonnet-4-6",
            system_prompt="You are an analyst.",
            user_prompt="Analyse this market.",
        )

    assert isinstance(result, dict)
    assert result["fair_probability"] == valid_input["fair_probability"]
    assert input_tokens == 120
    assert output_tokens == 80


@pytest.mark.asyncio
async def test_analyze_single_schema_failure_raises():
    # Response has no tool_use block — model responded with plain text
    content_blocks = [{"type": "text", "text": "I cannot help with that."}]
    mock_resp = make_mock_response(content_blocks)
    mock_resp.status_code = 200

    client = AnthropicAnalystClient(api_key="test-key")
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_resp
        with pytest.raises(ValueError, match="INVALID_OUTPUT"):
            await client.analyze_single(
                model="claude-sonnet-4-6",
                system_prompt="You are an analyst.",
                user_prompt="Analyse this market.",
            )
