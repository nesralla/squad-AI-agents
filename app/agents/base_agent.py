import json
import logging
import re

from app.core.llm import call_claude

logger = logging.getLogger(__name__)


class BaseAgent:
    def __init__(self, name: str, system_prompt: str):
        self.name = name
        self.system_prompt = system_prompt

    def run(self, prompt: str, max_tokens: int = 8000) -> str:
        return call_claude(self.system_prompt, prompt, max_tokens)

    @staticmethod
    def extract_json(raw: str, agent_name: str = "Agent") -> dict:
        """
        Extract a JSON object from LLM output.

        Handles:
          1. Complete JSON inside markdown fences (```json ... ```)
          2. Complete raw JSON object
          3. Truncated JSON inside an unclosed markdown fence
          4. Truncated raw JSON (best-effort repair)

        Raises ValueError if no valid JSON can be recovered.
        """
        # 1. Complete markdown fenced block
        json_match = re.search(r"```(?:json)?\s*(\{.+\})\s*```", raw, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # 2. Complete raw JSON object
        obj_match = re.search(r"\{.+\}", raw, re.DOTALL)
        if obj_match:
            try:
                return json.loads(obj_match.group())
            except json.JSONDecodeError:
                pass

        # 3. Truncated JSON inside unclosed markdown fence
        fence_start = re.search(r"```(?:json)?\s*(\{.+)", raw, re.DOTALL)
        if fence_start:
            logger.warning(f"{agent_name} JSON truncated inside markdown fence, attempting repair...")
            repaired = _try_repair_json(fence_start.group(1))
            if repaired:
                logger.info(f"{agent_name} JSON repair succeeded.")
                return repaired

        # 4. Try repair on raw text
        logger.warning(f"{agent_name} JSON parsing failed, attempting repair...")
        repaired = _try_repair_json(raw)
        if repaired:
            logger.info(f"{agent_name} JSON repair succeeded.")
            return repaired

        raise ValueError(f"{agent_name} returned non-JSON response:\n{raw[:500]}")


def _process_char(ch: str, prev_escape: bool, in_str: bool) -> tuple[bool, bool, str | None]:
    """Process a single char for JSON walking. Returns (in_string, escape_next, structural_char_or_none)."""
    if prev_escape:
        return in_str, False, None
    if ch == '\\' and in_str:
        return in_str, True, None
    if ch == '"':
        return not in_str, False, None
    if in_str:
        return in_str, False, None
    return in_str, False, ch


def _find_last_complete_object(text: str) -> int:
    """
    Walk JSON text tracking brace depth and string boundaries.
    Returns the index of the last `}` that closes a nested object
    (i.e. brace_depth drops back to 1), or -1 if none found.
    """
    last_complete = -1
    brace_depth = 0
    in_string = False
    escape_next = False

    for i, ch in enumerate(text):
        in_string, escape_next, structural = _process_char(ch, escape_next, in_string)
        if structural == '{':
            brace_depth += 1
        elif structural == '}':
            brace_depth -= 1
            if brace_depth == 1:
                last_complete = i

    return last_complete


def _try_parse_with_suffixes(text: str, suffixes: list[str]) -> dict | None:
    """Try to parse text + each suffix as JSON, return first success."""
    for suffix in suffixes:
        try:
            return json.loads(text + suffix)
        except json.JSONDecodeError:
            continue
    return None


def _try_repair_json(raw: str) -> dict | None:
    """
    Attempt to repair truncated JSON from LLM output.

    Strategy: walk the text tracking brace depth to find the last
    fully-closed nested object, then close the surrounding structure.
    """
    start = raw.find("{")
    if start == -1:
        return None

    text = raw[start:]

    # Try as-is first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Find last complete nested object and try closing the structure
    last_complete = _find_last_complete_object(text)
    if last_complete > 0:
        truncated = text[: last_complete + 1]
        result = _try_parse_with_suffixes(truncated, [
            '], "summary": "output truncado — conteudo parcial recuperado"}',
            ']}',
            '}',
        ])
        if result:
            return result

    # Brute-force close
    return _try_parse_with_suffixes(text, [
        '"}]}',
        '"}}',
        '"}],"summary":"truncated"}',
    ])
