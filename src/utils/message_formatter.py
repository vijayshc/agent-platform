"""Message formatter compatibility stub."""

import logging
from typing import Any, Dict, List, Optional
from config.config import MESSAGE_FORMAT

logger = logging.getLogger("text2sql.message_formatter")


class MessageFormatter:
    """Compatibility formatter for LLM messages and responses."""

    @staticmethod
    def format_messages(messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, format_type: Optional[str] = None) -> Any:
        """Format messages for target format. Returns messages directly for standard formats."""
        if format_type is None:
            format_type = MESSAGE_FORMAT

        if format_type != "llama":
            return messages

        # Minimal plain text chat serialization for llama format
        parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            parts.append(f"<|start_header_id|>{role}<|end_header_id|>\n{content}<|eot_id|>")
        return "\n".join(parts)

    @staticmethod
    def parse_llama_response(response_text: str, tools: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """Compatibility response parser returning standardized message dict."""
        return {
            "role": "assistant",
            "content": response_text or "",
            "tool_calls": None,
        }
