"""Claude: picks among pre-validated setups (Module 3 scoring + news) and runs post-trade studies (Module 9).

Claude never sets prices, leverage or size - those come from levels.py / risk.py. It can only choose
one of the validated options, or none.
"""

from __future__ import annotations

import json
import logging

import anthropic

from .config import Config

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"

PICK_SYSTEM = """You are the analyst for a disciplined crypto perpetual-futures compounding challenge on Hyperliquid.
Rule-based code has already run the BTC gate, the screener, the deep dive, direction triggers and the risk math.
You receive candidate coins with their data and a list of fully-validated trade options (entry/SL/TP/leverage
already computed and checked). Your job:

1. Rate news sentiment per candidate from the supplied headlines (count clearly bullish vs bearish items; ignore irrelevant ones).
2. Score and rank candidates the way the protocol does: prefer green/flat price, strong whale L/S (>2:1 preferred),
   growing OI, NORMAL/LOW IV, 2+ bullish news, clean support structure under the entry, few obstacles before TP.
3. Pick the single best option id, or "NONE" if nothing is worth taking. Passing is a valid, often correct, answer.
4. If the picked coin has BOTH a long and a short option (reversal signals present), put the other one in "alternate".
5. List concrete warnings (e.g. resistance between entry and TP, thin whale sample, OI history missing, news risk).

Only choose ids from the options list. Do not invent prices. Be concise and specific."""

PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "pick": {"type": "string", "description": "option id or NONE"},
        "alternate": {"type": "string", "description": "opposite-direction option id for the same coin, or NONE"},
        "confidence": {"type": "integer", "description": "0-100"},
        "news": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "coin": {"type": "string"},
                    "bullish": {"type": "integer"},
                    "bearish": {"type": "integer"},
                    "summary": {"type": "string"},
                },
                "required": ["coin", "bullish", "bearish", "summary"],
                "additionalProperties": False,
            },
        },
        "ranking": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"coin": {"type": "string"}, "score": {"type": "number"}, "note": {"type": "string"}},
                "required": ["coin", "score", "note"],
                "additionalProperties": False,
            },
        },
        "rationale": {"type": "string"},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["pick", "alternate", "confidence", "news", "ranking", "rationale", "warnings"],
    "additionalProperties": False,
}

STUDY_SYSTEM = """You review closed trades from a disciplined perpetual-futures compounding challenge.
Compare the planned entry/SL/TP with what price actually did afterwards (excursions and current levels are supplied).
Identify what went right or wrong, concretely. Propose a new rule ONLY if the evidence shows a repeatable pattern that
the existing rules don't already cover; otherwise set propose_rule to false. Rules must be short, testable statements."""

STUDY_SCHEMA = {
    "type": "object",
    "properties": {
        "what_happened": {"type": "string"},
        "went_right": {"type": "array", "items": {"type": "string"}},
        "went_wrong": {"type": "array", "items": {"type": "string"}},
        "lesson": {"type": "string"},
        "propose_rule": {"type": "boolean"},
        "rule_text": {"type": "string"},
    },
    "required": ["what_happened", "went_right", "went_wrong", "lesson", "propose_rule", "rule_text"],
    "additionalProperties": False,
}


class Analyst:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = anthropic.Anthropic(max_retries=3, timeout=300)

    def _call(self, system: str, payload: dict, schema: dict, max_tokens: int = 16000) -> dict:
        resp = self.client.beta.messages.create(
            model=self.cfg.anthropic_model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": json.dumps(payload, default=str)}],
            output_config={"effort": self.cfg.anthropic_effort, "format": {"type": "json_schema", "schema": schema}},
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("analyst declined the request")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError("analyst response truncated (max_tokens)")
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text)

    def pick(self, context: dict) -> dict:
        return self._call(PICK_SYSTEM, context, PICK_SCHEMA)

    def study(self, context: dict) -> dict:
        return self._call(STUDY_SYSTEM, context, STUDY_SCHEMA, max_tokens=8000)
