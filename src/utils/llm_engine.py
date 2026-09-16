import json
import logging
import threading
import time
import numpy as np
from config.config import (
    MAX_TOKENS, TEMPERATURE, MESSAGE_FORMAT
)
from src.utils.browser_llm_proxy import (
    build_browser_proxy_input,
    build_browser_proxy_tool_prompt,
    get_browser_llm_callback,
    is_browser_llm_latest_message_only,
    iter_text_chunks,
)
from src.utils.message_formatter import MessageFormatter
from src.utils.llm_connection_manager import build_openai_client, get_default

# Single source of truth: the model that writes the stored vectors must also be
# the one that embeds search queries, otherwise similarity is meaningless.
EMBEDDING_MODEL_NAME = 'all-MiniLM-L12-v2'

# Loading a sentence-transformers model is not safe to do concurrently inside
# one process: two threads racing through transformers' lazy/meta-device init
# can raise "Cannot copy out of meta tensor", after which the embedding call
# silently degrades to a random vector and the document is indexed uselessly.
# One process-wide lock plus a shared cache means the model is materialised
# exactly once, no matter how many LLMEngine instances (knowledge manager,
# vector-search warmup, request handlers) ask for it at the same time.
_EMBEDDING_MODEL_LOCK = threading.Lock()
_EMBEDDING_MODEL_CACHE = None
_RERANKING_MODEL_LOCK = threading.Lock()
_RERANKING_MODEL_CACHE = None


class _StructuredFunction:
    def __init__(self, data):
        self.name = data.get("name")
        self.arguments = data.get("arguments")


class _StructuredToolCall:
    def __init__(self, data):
        self.id = data.get("id")
        self.type = data.get("type", "function")
        self.function = _StructuredFunction(data.get("function") or {})


class _StructuredMessage:
    def __init__(self, data):
        self.content = data.get("content")
        self.role = data.get("role", "assistant")
        calls = data.get("tool_calls")
        self.tool_calls = [_StructuredToolCall(tc) for tc in calls] if calls else None


class _StructuredChoice:
    def __init__(self, data):
        self.message = _StructuredMessage(data)


class _StructuredCompletionResponse:
    """Adapts parsed tool/content response to the ChatCompletion schema."""
    def __init__(self, data):
        self.choices = [_StructuredChoice(data)]


class LLMEngine:
    """
    A centralized engine for handling LLM interactions across the application.
    This class manages the connection to the LLM provider and provides a unified
    interface for making LLM calls.
    """
    
    def __init__(self):
        """Initialize the LLM Engine, using the admin-configured default connection."""
        self.logger = logging.getLogger('text2sql.llm_engine')
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
            self.logger.info(f"Initializing LLM Engine with connection '{connection.name}' endpoint: {connection.base_url}")
            self.logger.info(f"Using model: {self.model_name}")
            self.logger.info("LLM Engine initialized successfully")
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
            self.logger.info(f"Initialized LLM Engine with connection '{connection.name}' endpoint: {connection.base_url}")
            return self.client
        raise RuntimeError("No LLM connection configured. Add a default connection in the admin LLM Manager.")

    def _resolve_gen_params(self, max_tokens=None, temperature=None):
        """Return (max_tokens, temperature) for this call.

        The connection's extra_body is merged into the request body by the SDK,
        so a value configured there overrides whatever the engine passes here.
        """
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
        """Get or initialize the sentence transformer model for embeddings

        Returns:
            SentenceTransformer: The initialized embedding model
        """
        if self.embedding_model is not None:
            return self.embedding_model

        global _EMBEDDING_MODEL_CACHE
        with _EMBEDDING_MODEL_LOCK:
            # Another instance may have finished loading while we waited.
            if _EMBEDDING_MODEL_CACHE is None:
                start_time = time.time()
                self.logger.info(f"Loading embedding model 'sentence-transformers/{EMBEDDING_MODEL_NAME}'")
                last_error = None
                # Loading can transiently fail ("Cannot copy out of meta
                # tensor"); a short retry is enough to materialise the weights.
                for attempt in range(3):
                    try:
                        from sentence_transformers import SentenceTransformer
                        _EMBEDDING_MODEL_CACHE = SentenceTransformer(EMBEDDING_MODEL_NAME)
                        last_error = None
                        break
                    except Exception as e:
                        last_error = e
                        self.logger.warning(
                            f"Embedding model load attempt {attempt + 1} failed: {e}"
                        )
                        time.sleep(0.5)
                if last_error is not None:
                    self.logger.error(f"Failed to load embedding model: {str(last_error)}", exc_info=True)
                    return None
                self.logger.info(f"Embedding model loaded in {time.time() - start_time:.2f}s")
            self.embedding_model = _EMBEDDING_MODEL_CACHE
        return self.embedding_model
    
    def generate_embedding(self, text: str):
        """Generate embedding for the given text
        
        Args:
            text (str): Text to generate embedding for
            
        Returns:
            numpy.ndarray or list: Embedding vector or None if failed
        """
        model = self.get_embedding_model()
        if not model or not text:
            self.logger.warning("Embedding model not available or empty text, using random embedding as fallback")
            return np.random.randn(384).tolist()
            
        try:
            start_time = time.time()
            # Generate embedding vector
            embedding = model.encode(text)
            
            self.logger.info(f"Generated embedding in {time.time() - start_time:.2f}s " +
                             f"with shape {embedding.shape}")
            return embedding
            
        except Exception as e:
            self.logger.error(f"Error generating embedding: {str(e)}", exc_info=True)
            # Fallback to random embeddings
            return np.random.randn(384).tolist()
    
    def get_reranking_model(self):
        """Get or initialize a cross-encoder model for more accurate reranking
        
        Returns:
            CrossEncoder: The initialized reranking model (cross-encoder)
        """
        if self.reranking_model is not None:
            return self.reranking_model

        global _RERANKING_MODEL_CACHE
        with _RERANKING_MODEL_LOCK:
            if _RERANKING_MODEL_CACHE is None:
                start_time = time.time()
                self.logger.info("Loading reranking model 'cross-encoder/ms-marco-MiniLM-L-6-v2'")
                try:
                    from sentence_transformers import CrossEncoder
                    _RERANKING_MODEL_CACHE = CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
                    self.logger.info(f"Reranking model loaded in {time.time() - start_time:.2f}s")
                except Exception as e:
                    self.logger.error(f"Failed to load reranking model: {str(e)}", exc_info=True)
                    return None
            self.reranking_model = _RERANKING_MODEL_CACHE
        return self.reranking_model
    
    def generate_completion(self, messages, log_prefix="LLM", max_tokens=None, temperature=None, stream=False):
        """
        Generate a completion using the LLM
        
        Args:
            messages (list): List of SystemMessage and UserMessage objects
            log_prefix (str, optional): Prefix for logging to identify the caller
            max_tokens (int, optional): Maximum tokens to generate, defaults to config value
            temperature (float, optional): Temperature for generation, defaults to config value
            stream (bool, optional): Whether to stream the response, defaults to False
            
        Returns:
            If stream=False: str: The generated completion text
            If stream=True: generator: A generator yielding text chunks
        """
        start_time = time.time()
        self.logger.info(f"[{log_prefix}] Completion generation started (format: {MESSAGE_FORMAT})")
        
        # Convert message formats to OpenAI format
        openai_messages = []
        for msg in messages:
            if isinstance(msg, dict):
                # Message is already in dictionary format
                if 'role' in msg and 'content' in msg:
                    openai_messages.append({
                        "role": msg['role'],
                        "content": msg['content']
                    })
                elif 'role' in msg:
                    # Handle cases where content might be None or missing
                    openai_messages.append({
                        "role": msg['role'],
                        "content": msg.get('content', '')
                    })
                else:
                    # Invalid message format, skip
                    self.logger.warning(f"[{log_prefix}] Skipping invalid message format: {msg}")
                    continue
            elif hasattr(msg, 'role') and hasattr(msg, 'content'):
                # Message object with attributes
                openai_messages.append({
                    "role": msg.role,
                    "content": msg.content
                })
            else:
                # Convert Azure/other format to OpenAI format
                if hasattr(msg, '__class__'):
                    if msg.__class__.__name__ == 'SystemMessage':
                        role = 'system'
                    elif msg.__class__.__name__ == 'UserMessage':
                        role = 'user'
                    else:
                        role = 'user'  # Default to user if unknown
                    
                    content = getattr(msg, 'content', '')
                    openai_messages.append({
                        "role": role,
                        "content": content
                    })
                else:
                    self.logger.warning(f"[{log_prefix}] Skipping unknown message format: {type(msg)}")
                    continue
        
        # Use provided values or defaults from the connection/config
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

            self.logger.info(f"[{log_prefix}] Routing completion through browser-local proxy")
            completion_text = browser_llm_callback(proxy_input, callback_metadata)

            if stream:
                def browser_response_generator():
                    for chunk in iter_text_chunks(completion_text):
                        yield chunk

                return browser_response_generator()

            return completion_text
        
        try:
            # Handle different message formats
            if MESSAGE_FORMAT == 'llama':
                # Format messages for Llama (no tools)
                formatted_prompt = MessageFormatter.format_messages(openai_messages, None, 'llama')
                request_messages = [{"role": "user", "content": formatted_prompt}]
            else:
                # Use OpenAI format as-is
                request_messages = openai_messages
            
            # Log the prompt message but truncate if too large
            prompt_str = str(request_messages)
            
            if len(prompt_str) > 500:
                self.logger.info(f"[{log_prefix}] Prompt: {prompt_str[:500]}... (truncated)")
            else:
                self.logger.info(f"[{log_prefix}] Prompt: {prompt_str}")
            
            self._ensure_client()
            self.logger.info(f"[{log_prefix}] Sending request to {self.model_name} with max_tokens={max_tokens}, temperature={temperature}, stream={stream}")
            call_start = time.time()
            
            # Handle streaming case
            self.logger.info(f"message {request_messages}")
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
                    
                    # Log at the end of streaming
                    call_duration = time.time() - call_start
                    full_response = "".join(collected_content)
                    self.logger.info(f"[{log_prefix}] Model streaming completed in {call_duration:.2f}s")
                    if len(full_response) > 500:
                        self.logger.info(f"[{log_prefix}] Raw model response: '{full_response[:500]}...' (truncated)")
                    else:
                        self.logger.info(f"[{log_prefix}] Raw model response: '{full_response}'")
                    
                    processing_time = time.time() - start_time
                    self.logger.info(f"[{log_prefix}] Completion generation completed in {processing_time:.2f}s")
                    self.logger.info(f"**************************")
                
                return response_generator()
            
            # Handle non-streaming case (original behavior)
            else:
                response = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=request_messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **self._extra_body_kwargs
                )
                
                call_duration = time.time() - call_start
                self.logger.info(f"[{log_prefix}] Model response received in {call_duration:.2f}s")
                
                # Extract response text
                completion_text = response.choices[0].message.content
                
                # Log truncated response if large
                if len(completion_text) > 500:
                    self.logger.info(f"[{log_prefix}] Raw model response: '{completion_text[:500]}...' (truncated)")
                else:
                    self.logger.info(f"[{log_prefix}] Raw model response: '{completion_text}'")
                    
                processing_time = time.time() - start_time
                self.logger.info(f"[{log_prefix}] Completion generation completed in {processing_time:.2f}s")
                self.logger.info(f"**************************")
                return completion_text
            
        except Exception as e:
            processing_time = time.time() - start_time
            self.logger.error(f"[{log_prefix}] Completion generation error after {processing_time:.2f}s: {str(e)}", exc_info=True)
            raise

    def _build_browser_proxy_tool_prompt(self, messages, tools=None, tool_choice="auto"):
        """Build a plain-text prompt for text-only browser-local tool-capable completions."""
        return build_browser_proxy_tool_prompt(messages, tools=tools, tool_choice=tool_choice)

    def _build_browser_proxy_tool_response(self, response_text, tools=None):
        """Convert plain-text browser-local proxy output into a response object with parsed tool calls."""
        parsed_message = MessageFormatter.parse_llama_response(str(response_text or '').strip(), tools)

        if (
            (not parsed_message.get('tool_calls'))
            and parsed_message.get('content')
            and 'tool_calls' in str(parsed_message.get('content'))
        ):
            reparsed_message = MessageFormatter.parse_llama_response(str(parsed_message.get('content') or '').strip(), tools)
            if reparsed_message.get('tool_calls'):
                parsed_message = reparsed_message

        self.logger.info(
            "[BROWSER_PROXY_TOOL_RESPONSE] Parsed browser-local response: has_tool_calls=%s, content_preview=%r",
            bool(parsed_message.get('tool_calls')),
            (str(parsed_message.get('content') or '')[:300]),
        )

        return _StructuredCompletionResponse(parsed_message)
    
    def generate_completion_with_tools(self, messages, tools=None, tool_choice="auto", log_prefix="LLM", max_tokens=None, temperature=None):
        """
        Generate a completion using the LLM with tool calling support
        
        Args:
            messages (list): List of messages in OpenAI format
            tools (list, optional): List of tool definitions for function calling
            tool_choice (str, optional): Tool choice strategy ("auto", "none", or specific tool)
            log_prefix (str, optional): Prefix for logging to identify the caller
            max_tokens (int, optional): Maximum tokens to generate, defaults to config value
            temperature (float, optional): Temperature for generation, defaults to config value
            
        Returns:
            OpenAI completion response object with tool calls if any
        """
        start_time = time.time()
        self.logger.info(f"[{log_prefix}] Tool-enabled completion generation started (format: {MESSAGE_FORMAT})")
        
        # Convert message format if needed
        openai_messages = []
        for msg in messages:
            if isinstance(msg, dict):
                # Message is already in dictionary format
                if 'role' in msg and 'content' in msg:
                    openai_messages.append(msg)
                elif 'role' in msg:
                    # Handle cases where content might be None or missing
                    openai_messages.append({
                        "role": msg['role'],
                        "content": msg.get('content', ''),
                        **{k: v for k, v in msg.items() if k not in ['role', 'content']}  # Preserve other fields like tool_calls
                    })
                else:
                    # Invalid message format, skip
                    self.logger.warning(f"[{log_prefix}] Skipping invalid message format: {msg}")
                    continue
            elif hasattr(msg, 'role') and hasattr(msg, 'content'):
                # Message object with attributes
                openai_messages.append({
                    "role": msg.role,
                    "content": msg.content
                })
            else:
                # Convert Azure/other format to OpenAI format
                if hasattr(msg, '__class__'):
                    if msg.__class__.__name__ == 'SystemMessage':
                        role = 'system'
                    elif msg.__class__.__name__ == 'UserMessage':
                        role = 'user'
                    else:
                        role = 'user'  # Default to user if unknown
                    
                    content = getattr(msg, 'content', '')
                    openai_messages.append({
                        "role": role,
                        "content": content
                    })
                else:
                    self.logger.warning(f"[{log_prefix}] Skipping unknown message format: {type(msg)}")
                    continue
        
        # Use provided values or defaults from the connection/config
        max_tokens, temperature = self._resolve_gen_params(max_tokens, temperature)
        openai_messages = self._prepend_system_instruction(openai_messages)

        browser_llm_callback = get_browser_llm_callback()
        if callable(browser_llm_callback):
            proxy_input = self._build_browser_proxy_tool_prompt(
                openai_messages,
                tools=tools,
                tool_choice=tool_choice,
            )
            callback_metadata = {
                "log_prefix": log_prefix,
                "tool_choice": tool_choice,
                "max_tokens": max_tokens,
                "temperature": temperature,
                "messages": openai_messages,
                "tools": tools or [],
            }

            self.logger.info(f"[{log_prefix}] Routing tool-enabled completion through browser-local proxy")
            completion_text = browser_llm_callback(proxy_input, callback_metadata)
            return self._build_browser_proxy_tool_response(completion_text, tools=tools)
        
        try:
            # Handle different message formats
            if MESSAGE_FORMAT == 'llama':
                return self._generate_llama_completion(openai_messages, tools, tool_choice, log_prefix, max_tokens, temperature, start_time)
            else:
                return self._generate_openai_completion(openai_messages, tools, tool_choice, log_prefix, max_tokens, temperature, start_time)
                
        except Exception as e:
            processing_time = time.time() - start_time
            self.logger.error(f"[{log_prefix}] Tool-enabled completion generation error after {processing_time:.2f}s: {str(e)}", exc_info=True)
            raise

    def _generate_openai_completion(self, openai_messages, tools, tool_choice, log_prefix, max_tokens, temperature, start_time):
        """Generate completion using OpenAI format."""
        self._ensure_client()
        # Log the prompt message but truncate if too large
        prompt_str = str(openai_messages)
        
        if len(prompt_str) > 500:
            self.logger.info(f"[{log_prefix}] Prompt: {prompt_str[:500]}... (truncated)")
        else:
            self.logger.info(f"[{log_prefix}] Prompt: {prompt_str}")
        
        # Prepare request parameters
        request_params = {
            "model": self.model_name,
            "messages": openai_messages,
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        request_params.update(self._extra_body_kwargs)
        
        # Add tools if provided
        if tools:
            request_params["tools"] = tools
            request_params["tool_choice"] = tool_choice
            self.logger.info(f"[{log_prefix}] Including {len(tools)} tools with choice: {tool_choice}")
        
        self.logger.info(f"[{log_prefix}] Sending request to {self.model_name} with max_tokens={max_tokens}, temperature={temperature}")
        call_start = time.time()
        
        response = self.client.chat.completions.create(**request_params)
        
        call_duration = time.time() - call_start
        self.logger.info(f"[{log_prefix}] Model response received in {call_duration:.2f}s")
        
        message = response.choices[0].message
        
        # Log response details
        if message.content:
            if len(message.content) > 500:
                self.logger.info(f"[{log_prefix}] Raw model response: '{message.content[:500]}...' (truncated)")
            else:
                self.logger.info(f"[{log_prefix}] Raw model response: '{message.content}'")
        
        if hasattr(message, 'tool_calls') and message.tool_calls:
            self.logger.info(f"[{log_prefix}] Model requested {len(message.tool_calls)} tool calls")
            
        processing_time = time.time() - start_time
        self.logger.info(f"[{log_prefix}] Tool-enabled completion generation completed in {processing_time:.2f}s")
        self.logger.info(f"**************************")
        
        return response

    def _generate_llama_completion(self, openai_messages, tools, tool_choice, log_prefix, max_tokens, temperature, start_time):
        """Generate completion using Llama format."""
        self._ensure_client()
        # Format messages for Llama
        formatted_prompt = MessageFormatter.format_messages(openai_messages, tools, 'llama')
        
        if len(formatted_prompt) > 500:
            self.logger.info(f"[{log_prefix}] Llama prompt: {formatted_prompt[:500]}... (truncated)")
        else:
            self.logger.info(f"[{log_prefix}] Llama prompt: {formatted_prompt}")
        
        # Prepare request for Llama (no tools in request params, they're in the prompt)
        request_params = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": formatted_prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        request_params.update(self._extra_body_kwargs)
        
        if tools:
            self.logger.info(f"[{log_prefix}] Including {len(tools)} tools in Llama prompt")
        
        self.logger.info(f"[{log_prefix}] Sending Llama request to {self.model_name} with max_tokens={max_tokens}, temperature={temperature}")
        call_start = time.time()

        self.logger.info(f"message {request_params}")
        
        response = self.client.chat.completions.create(**request_params)
        
        call_duration = time.time() - call_start
        self.logger.info(f"[{log_prefix}] Llama model response received in {call_duration:.2f}s")
        
        # Parse Llama response and convert to OpenAI format
        raw_content = response.choices[0].message.content
        
        if raw_content:
            if len(raw_content) > 500:
                self.logger.info(f"[{log_prefix}] Raw Llama response: '{raw_content[:500]}...' (truncated)")
            else:
                self.logger.info(f"[{log_prefix}] Raw Llama response: '{raw_content}'")
        
        # Parse the response to extract tool calls or regular content
        parsed_message = MessageFormatter.parse_llama_response(raw_content, tools)
        
        # Log what we parsed
        if 'tool_calls' in parsed_message and parsed_message['tool_calls']:
            self.logger.info(f"[{log_prefix}] Parsed {len(parsed_message['tool_calls'])} tool calls from LLaMA response")
            for i, tool_call in enumerate(parsed_message['tool_calls']):
                self.logger.info(f"[{log_prefix}] Tool call {i+1}: {tool_call['function']['name']}({tool_call['function']['arguments']})")
        else:
            self.logger.info(f"[{log_prefix}] No tool calls found in LLaMA response, treating as regular content")
        
        llama_response = _StructuredCompletionResponse(parsed_message)
        
        if llama_response.choices[0].message.tool_calls:
            self.logger.info(f"[{log_prefix}] Llama model requested {len(llama_response.choices[0].message.tool_calls)} tool calls")
        
        processing_time = time.time() - start_time
        self.logger.info(f"[{log_prefix}] Llama tool-enabled completion generation completed in {processing_time:.2f}s")
        self.logger.info(f"**************************")
        
        return llama_response

    def close(self):
        """Close the LLM Engine client connection"""
        self.logger.info("Closing LLM Engine client connection")
        # No explicit close method needed for OpenAI client