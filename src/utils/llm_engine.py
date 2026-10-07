"""Centralized engine for LLM interactions and embedding delegations."""

import logging
import time
from config.config import MAX_TOKENS, MESSAGE_FORMAT, TEMPERATURE
from src.utils.browser_llm_proxy import (
    build_browser_proxy_input,
    get_browser_llm_callback,
    is_browser_llm_latest_message_only,
    iter_text_chunks,
)
from src.utils.embeddings import (
    EMBEDDING_MODEL_NAME,
    generate_embedding as _generate_embedding,
    get_embedding_model as _get_embedding_model,
    get_reranking_model as _get_reranking_model,
)
from src.utils.llm_connection_manager import build_openai_client, get_default
from src.utils.message_formatter import MessageFormatter


class LLMEngine:
    """Centralized engine for handling LLM chat completions and embeddings."""

    def __init__(self):
        """Initialize the LLM Engine, using the admin-configured default connection."""
        self.logger = logging.getLogger("text2sql.llm_engine")
        self.client = None
        self.model_name = None
        self.llm_defaults = {}
        self.system_instruction = None
        self.embedding_model = None
        self.reranking_model = None

        connection = get_default()
        if connection is not None:
            self.client = build_openai_client(connection)
            self.model_name = getattr(self.client, "model_name", None) or connection.model_name
            self.llm_defaults = getattr(self.client, "_llm_defaults", None) or {}
            self.system_instruction = getattr(self.client, "_llm_system_instruction", None)
            self.logger.info("Initializing LLM Engine with connection '%s' endpoint: %s", connection.name, connection.base_url)
            self.logger.info("Using model: %s", self.model_name)
        else:
            self.logger.warning(
                "No default LLM connection configured in LLM Manager; "
                "LLM calls will fail until one is added in the admin LLM Manager."
            )

    def _ensure_client(self):
        """Ensure client is ready, checking for a default connection if not yet loaded."""
        if self.client is not None:
            return self.client
        connection = get_default()
        if connection is not None:
            self.client = build_openai_client(connection)
            self.model_name = getattr(self.client, "model_name", None) or connection.model_name
            self.llm_defaults = getattr(self.client, "_llm_defaults", None) or {}
            self.system_instruction = getattr(self.client, "_llm_system_instruction", None)
            self.logger.info("Initialized LLM Engine with connection '%s' endpoint: %s", connection.name, connection.base_url)
            return self.client
        raise RuntimeError("No LLM connection configured. Add a default connection in the admin LLM Manager.")

    def _resolve_gen_params(self, max_tokens=None, temperature=None):
        """Return (max_tokens, temperature) for this call."""
        if max_tokens is None:
            max_tokens = self.llm_defaults.get("max_tokens") or MAX_TOKENS
        if temperature is None:
            temperature = self.llm_defaults.get("temperature") or TEMPERATURE
        return max_tokens, temperature

    def _prepend_system_instruction(self, openai_messages):
        """Prepend the connection system instruction if set and not already present."""
        if not self.system_instruction:
            return openai_messages
        has_explicit_system = any(m.get("role") == "system" for m in openai_messages if isinstance(m, dict))
        if has_explicit_system:
            return openai_messages
        return [{"role": "system", "content": self.system_instruction}, *openai_messages]

    @property
    def _extra_body(self):
        return self.llm_defaults.get("extra_body") or None

    @property
    def _extra_body_kwargs(self):
        extra_body = self._extra_body
        return {"extra_body": extra_body} if extra_body else {}

    def get_embedding_model(self):
        """Get or initialize the sentence transformer model for embeddings."""
        if self.embedding_model is not None:
            return self.embedding_model
        self.embedding_model = _get_embedding_model()
        return self.embedding_model

    def generate_embedding(self, text: str):
        """Generate embedding vector for the given text. Returns None if unavailable."""
        return _generate_embedding(text)

    def get_reranking_model(self):
        """Get or initialize a cross-encoder model for semantic reranking."""
        if self.reranking_model is not None:
            return self.reranking_model
        self.reranking_model = _get_reranking_model()
        return self.reranking_model

    def generate_completion(self, messages, log_prefix="LLM", max_tokens=None, temperature=None, stream=False):
        """Generate a chat completion using the configured LLM."""
        start_time = time.time()
        self.logger.info("[%s] Completion generation started (format: %s)", log_prefix, MESSAGE_FORMAT)

        openai_messages = []
        for msg in messages:
            if isinstance(msg, dict):
                if "role" in msg and "content" in msg:
                    openai_messages.append({"role": msg["role"], "content": msg["content"]})
                elif "role" in msg:
                    openai_messages.append({"role": msg["role"], "content": msg.get("content", "")})
                else:
                    self.logger.warning("[%s] Skipping invalid message format: %s", log_prefix, msg)
            elif hasattr(msg, "role") and hasattr(msg, "content"):
                openai_messages.append({"role": msg.role, "content": msg.content})
            elif hasattr(msg, "__class__"):
                role = "system" if msg.__class__.__name__ == "SystemMessage" else "user"
                openai_messages.append({"role": role, "content": getattr(msg, "content", "")})
            else:
                self.logger.warning("[%s] Skipping unknown message format: %s", log_prefix, type(msg))

        max_tokens, temperature = self._resolve_gen_params(max_tokens, temperature)
        openai_messages = self._prepend_system_instruction(openai_messages)

        browser_llm_callback = get_browser_llm_callback()
        if callable(browser_llm_callback):
            proxy_input = build_browser_proxy_input(
                openai_messages,
                latest_message_only=is_browser_llm_latest_message_only(),
            )
            callback_metadata = {
                "log_prefix": log_prefix,
                "stream": stream,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": openai_messages,
            }
            self.logger.info("[%s] Routing completion through browser-local proxy", log_prefix)
            completion_text = browser_llm_callback(proxy_input, callback_metadata)

            if stream:
                def browser_response_generator():
                    for chunk in iter_text_chunks(completion_text):
                        yield chunk
                return browser_response_generator()

            return completion_text

        try:
            if MESSAGE_FORMAT == "llama":
                formatted_prompt = MessageFormatter.format_messages(openai_messages, None, "llama")
                request_messages = [{"role": "user", "content": formatted_prompt}]
            else:
                request_messages = openai_messages

            prompt_str = str(request_messages)
            if len(prompt_str) > 500:
                self.logger.info("[%s] Prompt: %s... (truncated)", log_prefix, prompt_str[:500])
            else:
                self.logger.info("[%s] Prompt: %s", log_prefix, prompt_str)

            self._ensure_client()
            self.logger.info(
                "[%s] Sending request to %s with max_tokens=%s, temperature=%s, stream=%s",
                log_prefix, self.model_name, max_tokens, temperature, stream
            )
            call_start = time.time()

            if stream:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=request_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=True,
                    **self._extra_body_kwargs
                )

                def response_generator():
                    collected_content = []
                    for chunk in response:
                        if chunk.choices and len(chunk.choices) > 0:
                            content = chunk.choices[0].delta.content
                            if content:
                                collected_content.append(content)
                                yield content
                    call_duration = time.time() - call_start
                    full_response = "".join(collected_content)
                    self.logger.info("[%s] Model streaming completed in %.2fs", log_prefix, call_duration)
                    if len(full_response) > 500:
                        self.logger.info("[%s] Raw model response: '%s...' (truncated)", log_prefix, full_response[:500])
                    else:
                        self.logger.info("[%s] Raw model response: '%s'", log_prefix, full_response)
                    self.logger.info("[%s] Completion generation completed in %.2fs", log_prefix, time.time() - start_time)

                return response_generator()
            else:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=request_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **self._extra_body_kwargs
                )
                call_duration = time.time() - call_start
                self.logger.info("[%s] Model response received in %.2fs", log_prefix, call_duration)
                completion_text = response.choices[0].message.content
                if len(completion_text) > 500:
                    self.logger.info("[%s] Raw model response: '%s...' (truncated)", log_prefix, completion_text[:500])
                else:
                    self.logger.info("[%s] Raw model response: '%s'", log_prefix, completion_text)
                self.logger.info("[%s] Completion generation completed in %.2fs", log_prefix, time.time() - start_time)
                return completion_text

        except Exception as e:
            self.logger.error("[%s] Completion generation error after %.2fs: %s", log_prefix, time.time() - start_time, e, exc_info=True)
            raise

    def close(self):
        """Close the LLM Engine client connection."""
        self.logger.info("Closing LLM Engine client connection")


__all__ = ["LLMEngine", "EMBEDDING_MODEL_NAME"]