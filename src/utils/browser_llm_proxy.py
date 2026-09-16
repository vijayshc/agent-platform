"""Utilities for routing LLM requests through a browser-local proxy."""

from __future__ import annotations

import copy
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Callable, Dict, Iterator, Optional


_browser_llm_callback_var: ContextVar[Optional[Callable[[str, Optional[Dict[str, Any]]], str]]] = ContextVar(
    'browser_llm_callback',
    default=None,
)
_browser_llm_latest_message_only_var: ContextVar[bool] = ContextVar(
    'browser_llm_latest_message_only',
    default=False,
)


def _utc_now_iso() -> str:
    """Return the current UTC timestamp in ISO 8601 format."""
    return datetime.utcnow().isoformat() + 'Z'


def parse_llm_routing_mode(routing_mode: str, default_enabled: bool = False) -> bool:
    """Return whether browser-local proxy mode should be used for a request."""
    normalized_mode = (routing_mode or '').strip().lower()

    if normalized_mode in ('backend', 'api'):
        return False
    if normalized_mode in ('browser_proxy', 'proxy', 'local_proxy'):
        return True
    return bool(default_enabled)


def normalize_messages(messages: Any) -> list[dict[str, str]]:
    """Normalize assorted message formats into a simple role/content list."""
    if messages is None:
        return []

    if isinstance(messages, str):
        return [{'role': 'user', 'content': messages}]

    normalized_messages: list[dict[str, str]] = []
    for message in messages:
        if isinstance(message, dict):
            role = str(message.get('role', 'user') or 'user')
            content = message.get('content', '')
        else:
            role = getattr(message, 'role', 'user') or 'user'
            content = getattr(message, 'content', '')

            if not getattr(message, 'role', None) and hasattr(message, '__class__'):
                class_name = message.__class__.__name__
                if class_name == 'SystemMessage':
                    role = 'system'
                elif class_name == 'UserMessage':
                    role = 'user'
                elif class_name == 'AssistantMessage':
                    role = 'assistant'

        if content is None:
            content = ''

        normalized_messages.append({
            'role': str(role),
            'content': str(content),
        })

    return normalized_messages


def build_browser_proxy_input(messages: Any, latest_message_only: bool = False) -> str:
    """Build a readable text prompt for the browser-local proxy."""
    normalized_messages = normalize_messages(messages)
    if not normalized_messages:
        return ''

    selected_messages = [message for message in normalized_messages if message.get('content')]

    if latest_message_only:
        system_messages = [message for message in selected_messages if message.get('role') == 'system']
        latest_user_message = next(
            (
                message
                for message in reversed(selected_messages)
                if message.get('role') == 'user' and message.get('content')
            ),
            None,
        )

        if latest_user_message:
            selected_messages = system_messages + [latest_user_message]
        else:
            selected_messages = system_messages or selected_messages[-1:]

    prompt_sections = []
    for message in selected_messages:
        prompt_sections.append(f"{message['role'].upper()}:\n{message['content']}")

    return '\n\n'.join(prompt_sections)


def iter_text_chunks(text: str, chunk_size: int = 160) -> Iterator[str]:
    """Yield a string in reasonably sized chunks for faux streaming responses."""
    if text is None:
        return

    text = str(text)
    if not text:
        yield ''
        return

    start = 0
    text_length = len(text)
    while start < text_length:
        end = min(start + chunk_size, text_length)
        if end < text_length:
            whitespace_index = text.rfind(' ', start, end)
            if whitespace_index > start:
                end = whitespace_index + 1
        yield text[start:end]
        start = end


@contextmanager
def browser_llm_callback_context(
    callback: Optional[Callable[[str, Optional[Dict[str, Any]]], str]],
    *,
    latest_message_only: bool = False,
):
    """Temporarily register a browser-local LLM callback for the current context."""
    callback_token = _browser_llm_callback_var.set(callback)
    latest_only_token = _browser_llm_latest_message_only_var.set(bool(latest_message_only))
    try:
        yield
    finally:
        _browser_llm_callback_var.reset(callback_token)
        _browser_llm_latest_message_only_var.reset(latest_only_token)


def get_browser_llm_callback() -> Optional[Callable[[str, Optional[Dict[str, Any]]], str]]:
    """Return the active browser-local LLM callback if one is registered."""
    return _browser_llm_callback_var.get()


def is_browser_llm_latest_message_only() -> bool:
    """Return whether only the latest user message should be sent to the proxy."""
    return bool(_browser_llm_latest_message_only_var.get())


class BrowserLLMResponseManager:
    """Coordinate pending browser-local LLM requests and their responses."""

    def __init__(self):
        self._requests: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create_request(self, input_text: str, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Register a pending request and return the payload to expose to the browser."""
        request_id = str(uuid.uuid4())
        event = threading.Event()

        payload = {
            'request_id': request_id,
            'input': str(input_text or ''),
            'status': 'pending',
            'created_at': _utc_now_iso(),
        }

        with self._lock:
            self._requests[request_id] = {
                'event': event,
                'payload': payload,
                'response': None,
                'error': None,
            }

        return copy.deepcopy(payload)

    def submit_response(self, request_id: str, *, response: Optional[str] = None, error: Optional[str] = None) -> bool:
        """Store the browser-local LLM response for a pending request."""
        with self._lock:
            request_entry = self._requests.get(request_id)
            if not request_entry:
                return False

            payload = request_entry['payload']
            payload['received_at'] = _utc_now_iso()

            if error:
                payload['status'] = 'failed'
                request_entry['error'] = str(error)
            else:
                payload['status'] = 'completed'
                request_entry['response'] = str(response or '')

            request_entry['event'].set()
            return True

    def wait_for_response(self, request_id: str, timeout_seconds: float) -> str:
        """Wait for a response for the given request ID and return it."""
        with self._lock:
            request_entry = self._requests.get(request_id)
            if not request_entry:
                raise RuntimeError('Browser-local LLM request not found')

            event = request_entry['event']

        if not event.wait(timeout_seconds):
            with self._lock:
                self._requests.pop(request_id, None)
            raise TimeoutError(f'Timed out waiting for browser-local LLM response after {timeout_seconds}s')

        with self._lock:
            request_entry = self._requests.pop(request_id, None)

        if not request_entry:
            raise RuntimeError('Browser-local LLM request was cleared before completion')

        if request_entry.get('error'):
            raise RuntimeError(str(request_entry['error']))

        return str(request_entry.get('response') or '')


browser_llm_response_manager = BrowserLLMResponseManager()


class BrowserLLMOperationStore:
    """Thread-safe in-memory store for long-running browser-proxy-backed operations."""

    def __init__(self):
        self._operations: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create(self, initial_data: Optional[Dict[str, Any]] = None) -> str:
        """Create a new operation and return its ID."""
        operation_id = str(uuid.uuid4())
        payload = {
            'status': 'started',
            'created_at': _utc_now_iso(),
        }
        if initial_data:
            payload.update(copy.deepcopy(initial_data))

        with self._lock:
            self._operations[operation_id] = payload

        return operation_id

    def update(self, operation_id: str, **updates: Any) -> bool:
        """Update an operation in place."""
        with self._lock:
            if operation_id not in self._operations:
                return False
            self._operations[operation_id].update(copy.deepcopy(updates))
            return True

    def get(self, operation_id: str) -> Optional[Dict[str, Any]]:
        """Return a deep copy of the stored operation payload."""
        with self._lock:
            operation = self._operations.get(operation_id)
            return copy.deepcopy(operation) if operation is not None else None

    def delete(self, operation_id: str) -> bool:
        """Delete an operation from the store."""
        with self._lock:
            return self._operations.pop(operation_id, None) is not None


def create_blocking_browser_llm_callback(
    *,
    on_request_created: Optional[Callable[[Dict[str, Any], Optional[Dict[str, Any]]], None]] = None,
    on_request_cleared: Optional[Callable[[Dict[str, Any], Optional[Dict[str, Any]]], None]] = None,
    timeout_seconds: float = 120.0,
) -> Callable[[str, Optional[Dict[str, Any]]], str]:
    """Create a synchronous callback that waits for a browser-local proxy response."""

    def callback(input_text: str, metadata: Optional[Dict[str, Any]] = None) -> str:
        pending_request = browser_llm_response_manager.create_request(input_text, metadata=metadata)

        if on_request_created:
            on_request_created(copy.deepcopy(pending_request), copy.deepcopy(metadata) if metadata else None)

        try:
            return browser_llm_response_manager.wait_for_response(
                pending_request['request_id'],
                timeout_seconds=timeout_seconds,
            )
        finally:
            if on_request_cleared:
                on_request_cleared(copy.deepcopy(pending_request), copy.deepcopy(metadata) if metadata else None)

    return callback


def build_browser_proxy_tool_prompt(messages: Any, tools: Optional[list] = None, tool_choice: str = "auto") -> str:
    """Build a plain-text prompt for text-only browser-local tool-capable completions."""
    import json

    sections = [
        "You are responding through a text-only local browser proxy.",
        "Return plain text only.",
        "If you need to call a tool, return ONLY raw JSON with no markdown fences using this exact structure:",
        '{"tool_calls": [{"name": "tool_name", "arguments": {}}]}',
        "If no tool is needed, return a normal assistant response as plain text.",
        f"Requested tool_choice: {tool_choice}",
    ]

    if tools:
        simplified_tools = []
        for tool in tools:
            function_def = tool.get('function', {}) if isinstance(tool, dict) else {}
            if not function_def:
                continue
            simplified_tools.append({
                'name': function_def.get('name', ''),
                'description': function_def.get('description', ''),
                'parameters': function_def.get('parameters', {}),
            })

        if simplified_tools:
            sections.append("Available tools (JSON):")
            sections.append(json.dumps(simplified_tools, indent=2))

    conversation_lines = []
    for msg in messages:
        role = str(msg.get('role', 'user') or 'user').upper()
        content = msg.get('content')
        tool_calls = msg.get('tool_calls')

        if role == 'ASSISTANT' and tool_calls:
            conversation_lines.append(f"{role}_TOOL_CALLS:\n{json.dumps(tool_calls, indent=2)}")

        if content is not None and str(content).strip():
            conversation_lines.append(f"{role}:\n{content}")

        if role == 'TOOL':
            tool_name = msg.get('name', 'tool')
            tool_call_id = msg.get('tool_call_id', '')
            label = f"TOOL_RESULT ({tool_name}" + (f", {tool_call_id}" if tool_call_id else "") + ")"
            conversation_lines.append(f"{label}:\n{content or ''}")

    if conversation_lines:
        sections.append("Conversation:")
        sections.append("\n\n".join(conversation_lines))

    return "\n\n".join(section for section in sections if section)