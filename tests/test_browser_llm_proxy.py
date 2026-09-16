import threading
import time

import pytest

from src.utils.browser_llm_proxy import (
    BrowserLLMOperationStore,
    BrowserLLMResponseManager,
    build_browser_proxy_input,
    parse_llm_routing_mode,
)


def test_parse_llm_routing_mode_respects_explicit_values():
    assert parse_llm_routing_mode('backend', default_enabled=True) is False
    assert parse_llm_routing_mode('browser_proxy', default_enabled=False) is True
    assert parse_llm_routing_mode('', default_enabled=True) is True
    assert parse_llm_routing_mode(None, default_enabled=False) is False


def test_build_browser_proxy_input_keeps_full_message_history_by_default():
    prompt = build_browser_proxy_input([
        {'role': 'system', 'content': 'You are helpful.'},
        {'role': 'user', 'content': 'First question'},
        {'role': 'assistant', 'content': 'First answer'},
        {'role': 'user', 'content': 'Second question'},
    ])

    assert 'SYSTEM:\nYou are helpful.' in prompt
    assert 'USER:\nFirst question' in prompt
    assert 'ASSISTANT:\nFirst answer' in prompt
    assert 'USER:\nSecond question' in prompt


def test_build_browser_proxy_input_uses_only_latest_user_message_when_requested():
    prompt = build_browser_proxy_input([
        {'role': 'system', 'content': 'Keep context instructions.'},
        {'role': 'user', 'content': 'Earlier question'},
        {'role': 'assistant', 'content': 'Earlier answer'},
        {'role': 'user', 'content': 'Latest question'},
    ], latest_message_only=True)

    assert 'SYSTEM:\nKeep context instructions.' in prompt
    assert 'USER:\nLatest question' in prompt
    assert 'Earlier question' not in prompt
    assert 'Earlier answer' not in prompt


def test_browser_llm_response_manager_returns_submitted_response():
    manager = BrowserLLMResponseManager()
    pending_request = manager.create_request('hello')

    def complete_request():
        time.sleep(0.05)
        manager.submit_response(pending_request['request_id'], response='world')

    threading.Thread(target=complete_request, daemon=True).start()

    assert manager.wait_for_response(pending_request['request_id'], 1.0) == 'world'


def test_browser_llm_response_manager_raises_for_error_response():
    manager = BrowserLLMResponseManager()
    pending_request = manager.create_request('hello')

    def fail_request():
        time.sleep(0.05)
        manager.submit_response(pending_request['request_id'], error='boom')

    threading.Thread(target=fail_request, daemon=True).start()

    with pytest.raises(RuntimeError, match='boom'):
        manager.wait_for_response(pending_request['request_id'], 1.0)


def test_browser_llm_operation_store_round_trip():
    store = BrowserLLMOperationStore()
    operation_id = store.create({'status': 'processing', 'message': 'hi'})

    stored = store.get(operation_id)
    assert stored['status'] == 'processing'
    assert stored['message'] == 'hi'

    assert store.update(operation_id, status='completed', result={'answer': 'done'}) is True
    stored = store.get(operation_id)
    assert stored['status'] == 'completed'
    assert stored['result']['answer'] == 'done'

    assert store.delete(operation_id) is True
    assert store.get(operation_id) is None