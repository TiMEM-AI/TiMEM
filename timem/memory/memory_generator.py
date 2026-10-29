"""
TiMem Memory Generator
Responsible for calling LLM to generate memory summaries at different levels.
"""
from typing import Any, Dict, List, Optional
import asyncio
import random

from llm.base_llm import BaseLLM
import os
import json
from datetime import datetime
from llm.llm_manager import get_llm
from timem.utils.prompt_manager import get_prompt_manager
from timem.utils.logging import get_logger
from timem.utils.config_manager import get_config

logger = get_logger(__name__)

class MemoryGenerator:
    """
    Memory generator for generating and processing memory content at different levels.
    """
    def __init__(self, llm_provider: Optional[str] = None, debug_mode: bool = False):
        if debug_mode:
            print(f"\n[MemoryGenerator] Starting initialization, LLM provider: {llm_provider or 'default'}")
        
        # Get LLM instance
        self.llm: BaseLLM = get_llm(llm_provider)
        llm_type = type(self.llm).__name__
        if debug_mode:
            print(f"[MemoryGenerator] Got LLM instance: {llm_type}")
        
        # For ZhipuAI adapter, check if client is initialized
        if "zhipuai" in llm_type.lower() and debug_mode:
            if hasattr(self.llm, "client") and self.llm.client:
                print(f"[MemoryGenerator] ZhipuAI client initialized")
            else:
                print(f"[MemoryGenerator] Warning: ZhipuAI client not initialized, may cause subsequent LLM calls to fail")
        
        # Get prompt manager
        self.prompt_manager = get_prompt_manager()
        if debug_mode:
            print(f"[MemoryGenerator] Got prompt manager")
        
        # Check and reload prompt configuration if needed
        self._ensure_prompts_loaded(debug_mode)
        
        # [INFO] Get global language configuration
        self.global_language = self._get_global_language()
        if debug_mode:
            print(f"[MemoryGenerator] Global language configuration: {self.global_language}")
        
        # Check prompt availability
        if hasattr(self.prompt_manager, "debug_prompt_keys") and debug_mode:
            debug_info = self.prompt_manager.debug_prompt_keys()
            print(f"[MemoryGenerator] Prompt configuration info:")
            print(f"   - Current language: {debug_info.get('current_language', 'unknown')}")
            print(f"   - Total prompts: {debug_info.get('total_prompts', 0)}")
            
            # Check if critical prompts exist
            critical_keys = [
                "l2_session_summary",     # L2 prompt
                "l3_daily_summary",       # L3 prompt
                "l4_weekly_summary",      # L4 prompt
                "l5_high_level_summary"   # L5 prompt
            ]
            
            available_keys = debug_info.get("available_keys", [])
            for key in critical_keys:
                if key in available_keys:
                    print(f"   - [OK] Critical prompt '{key}' available")
                else:
                    print(f"   - [ERROR] Error: Critical prompt '{key}' does not exist!")
            
            # Print all available prompt keys
            print(f"   - All available prompt keys: {available_keys}")
        else:
            print(f"[WARNING] Unable to check prompt configuration, debug_prompt_keys method not available")
            
            # Try to directly check L2 prompt
            l2_key = "l2_session_summary"
            l2_prompt = self.prompt_manager.get_prompt(l2_key)
            if l2_prompt:
                print(f"[OK] L2 prompt '{l2_key}' available")
            else:
                print(f"[ERROR] Critical error: L2 prompt '{l2_key}' not available!")
        
        # Record the last final prompt used to call LLM for test reading
        self._last_prompt: Optional[str] = None
        
        # [NEW] Record the last ChatResponse from LLM call for extracting token information
        self._last_chat_response: Optional[Any] = None
        self._last_chat_responses: List[Any] = []  # Record all ChatResponses in one generation
        
        # Load retry configuration
        self._load_retry_config()
        print(f"[OK] MemoryGenerator initialization complete")
    
    def _ensure_prompts_loaded(self, debug_mode: bool = False):
        """Ensure prompt configuration is loaded correctly, retry if failed"""
        if debug_mode:
            print(f"[MemoryGenerator] Checking prompt configuration...")
        
        # Check if critical prompts are available
        critical_keys = [
            "l2_session_summary",     # L2 prompt
            "l3_daily_summary",       # L3 prompt
            "l4_weekly_summary",      # L4 prompt
            "l5_high_level_summary"   # L5 prompt
        ]
        
        missing_keys = []
        for key in critical_keys:
            prompt = self.prompt_manager.get_prompt(key)
            if not prompt:
                missing_keys.append(key)
        
        if missing_keys:
            if debug_mode:
                print(f"[MemoryGenerator] Found missing prompts: {missing_keys}")
                print(f"[MemoryGenerator] Attempting to reload prompt configuration...")
            
            # Try to reload prompt configuration
            from timem.utils.prompt_manager import reload_prompt_manager
            try:
                self.prompt_manager = reload_prompt_manager()
                if debug_mode:
                    print(f"[MemoryGenerator] Prompt configuration reloaded successfully")
                
                # Check critical prompts again
                still_missing = []
                for key in critical_keys:
                    prompt = self.prompt_manager.get_prompt(key)
                    if not prompt:
                        still_missing.append(key)
                
                if still_missing:
                    if debug_mode:
                        print(f"[MemoryGenerator] Still missing prompts after reload: {still_missing}")
                else:
                    if debug_mode:
                        print(f"[MemoryGenerator] [OK] All critical prompts are available")
                        
            except Exception as e:
                if debug_mode:
                    print(f"[MemoryGenerator] Failed to reload prompt configuration: {e}")
        else:
            if debug_mode:
                print(f"[MemoryGenerator] [OK] All critical prompts are available")

    def _load_retry_config(self):
        """Load retry and timeout configuration"""
        config = get_config()
        workflows_config = config.get("workflows", {})
        memory_gen_config = workflows_config.get("memory_generation", {})
        
        # Load layer timeout configuration
        layer_timeouts = memory_gen_config.get("layer_timeouts", {})
        self.layer_timeouts = {
            "l1": layer_timeouts.get("l1", 60),
            "l2": layer_timeouts.get("l2", 120),
            "l3": layer_timeouts.get("l3", 300),
            "l4": layer_timeouts.get("l4", 600),
            "l5": layer_timeouts.get("l5", 900),
        }
        
        # Load retry configuration - set to infinite retry
        # Use a very large number as "infinite" retry to ensure memory generation eventually succeeds
        self.max_retries = memory_gen_config.get("retry_count", 999999)  # Effectively infinite retry
        retry_config = memory_gen_config.get("retry_config", {})
        self.base_delay = retry_config.get("base_delay", 1.0)
        self.max_delay = retry_config.get("max_delay", 60.0)
        self.backoff_factor = retry_config.get("backoff_factor", 2.0)
        self.jitter = retry_config.get("jitter", True)
        
        logger.info(f"Retry configuration loaded: max_retries={self.max_retries if self.max_retries < 999999 else '∞(infinite)'}, base_delay={self.base_delay}s")

    def _get_global_language(self) -> str:
        """Get global language configuration"""
        try:
            app_config = get_config("app")
            language = app_config.get("language", "en")
            return language.lower()
        except Exception as e:
            logger.warning(f"Failed to get global language configuration: {e}, using default 'en'")
            return "en"

    async def _retry_with_exponential_backoff_for_streaming(self, operation_func, layer: str, operation_name: str):
        """
        Retry mechanism designed specifically for streaming generation
        Only timeout control for first token reception, no timeout once token reception starts
        """
        connection_timeout = 30  # Only 30-second timeout for first token connection
        consecutive_connection_failures = 0
        
        attempt = 0
        while True:  # True infinite retry
            try:
                # Execute streaming generation function directly without additional timeout control
                # The streaming generation function handles first token reception internally
                result = await operation_func()
                
                if attempt > 0:
                    logger.info(f"[OK] {operation_name} attempt {attempt + 1} succeeded")
                return result
                
            except asyncio.TimeoutError as e:
                # Only retry on actual connection timeout
                if "first token reception timeout" in str(e) or "connection" in str(e).lower():
                    attempt += 1
                    logger.warning(f"[TIMEOUT]  {operation_name} attempt {attempt} connection timeout ({connection_timeout}s)")
                    delay = self._calculate_backoff_delay(attempt)
                    logger.warning(f"[RETRY] Retrying after {delay:.1f}s (connection timeout)")
                    await asyncio.sleep(delay)
                    continue
                else:
                    # Other timeout errors (e.g., total streaming generation timeout) do not retry
                    raise
                    
            except Exception as e:
                attempt += 1
                error_msg = str(e)
                error_msg_lower = error_msg.lower()
                
                # [SEARCH] Precisely identify error type
                is_connection_error = any(keyword in error_msg_lower for keyword in [
                    "cannot connect", "connection", "connect timeout", 
                    "connection refused", "connection reset", "network",
                    "connection timeout to host", "connection aborted"
                ])
                
                is_timeout_error = any(keyword in error_msg_lower for keyword in [
                    "timeout", "timed out", "time out", "timeout"
                ]) and "connection" in error_msg_lower
                
                # Only retry on connection-related errors
                if is_connection_error or is_timeout_error:
                    consecutive_connection_failures += 1
                    if consecutive_connection_failures <= 5:
                        delay = 0.05
                        logger.warning(f"[WARN]  {operation_name} attempt {attempt} failed: {error_msg[:100]}")
                        logger.warning(f"[RETRY] Retrying after {delay:.1f}s (connection issue)")
                        await asyncio.sleep(delay)
                        continue
                
                # Content generation failures and other errors also retry
                if "return content" in error_msg or "generation failed" in error_msg:
                    delay = self._calculate_backoff_delay(attempt)
                    logger.error(f"[ERROR] {operation_name} attempt {attempt} failed: {error_msg[:100]}")
                    logger.warning(f"[RETRY] Retrying after {delay:.1f}s")
                    await asyncio.sleep(delay)
                    continue
                
                # Unrecoverable errors are thrown directly
                raise

    async def _retry_with_exponential_backoff(self, operation_func, layer: str, operation_name: str):
        """
        Retry mechanism with exponential backoff (engineering-grade - intelligent retry)
        
        Args:
            operation_func: Async operation function to retry
            layer: Layer identifier (l1, l2, l3, l4, l5)
            operation_name: Operation name for logging
        
        Returns:
            Operation result
        """
        # Separate connection timeout and generation timeout
        connection_timeout = 30  # Connection timeout 30 seconds (first token timeout)
        generation_timeout = self.layer_timeouts.get(layer.lower(), 300)  # Generation timeout (entire streaming generation)
        consecutive_connection_failures = 0  # Consecutive connection failures
        
        attempt = 0
        while True:  # True infinite retry
            try:
                # Use asyncio.wait_for to add connection timeout control (only for first token)
                result = await asyncio.wait_for(operation_func(), timeout=connection_timeout)
                if attempt > 0:
                    logger.info(f"[OK] {operation_name} attempt {attempt + 1} succeeded")
                return result
                
            except asyncio.TimeoutError:
                attempt += 1
                logger.warning(f"[TIMEOUT]  {operation_name} attempt {attempt} connection timeout ({connection_timeout}s)")
                delay = self._calculate_backoff_delay(attempt)
                logger.warning(f"[RETRY] Retrying after {delay:.1f}s (connection timeout)")
                await asyncio.sleep(delay)
                continue
                    
            except Exception as e:
                attempt += 1
                error_msg = str(e)
                error_msg_lower = error_msg.lower()
                
                # [SEARCH] Precisely identify error type
                is_connection_error = any(keyword in error_msg_lower for keyword in [
                    "cannot connect", "connection", "connect timeout", 
                    "connection refused", "connection reset", "network",
                    "connection timeout to host", "connection aborted"
                ])
                
                is_timeout_error = any(keyword in error_msg_lower for keyword in [
                    "timeout", "timed out", "time out", "timeout"
                ])
                
                is_rate_limit_error = any(keyword in error_msg_lower for keyword in [
                    "rate limit", "too many requests", "429"
                ])
                
                is_server_error = any(keyword in error_msg_lower for keyword in [
                    "500", "502", "503", "504", "service unavailable", "internal error", "bad gateway", "service unavailable"
                ])
                
                # [FIX] New: ZhipuAI-specific service-side exception detection
                is_zhipuai_service_error = any(keyword in error_msg_lower for keyword in [
                    "open.bigmodel.cn", "zhipuai", "zhipu", "bigmodel"
                ]) and (is_connection_error or is_timeout_error)
                
                # Use different retry strategies for different error types
                if is_zhipuai_service_error:
                    # [FIX] ZhipuAI service-side exception: immediate retry strategy
                    consecutive_connection_failures += 1
                    if consecutive_connection_failures <= 5:
                        # First 5 failures: immediate retry (ZhipuAI service exceptions are usually temporary)
                        delay = 0.05  # Retry immediately after 50ms
                    elif consecutive_connection_failures <= 10:
                        # 6-10 failures: short delay retry
                        delay = 0.5
                    elif consecutive_connection_failures <= 15:
                        # 11-15 failures: medium delay
                        delay = 2.0
                    else:
                        # Continuous failures: use exponential backoff with 10s cap
                        base_delay = min(3.0 * (consecutive_connection_failures - 15), 10.0)
                        delay = base_delay + self._calculate_backoff_delay(attempt - 15)
                    
                    logger.error(f"[AI] {operation_name} attempt {attempt} ZhipuAI service exception (consecutive {consecutive_connection_failures}): {error_msg[:100]}")
                    logger.warning(f"[RETRY] Retrying after {delay:.1f}s (ZhipuAI service exception, immediate retry)")
                    
                elif is_connection_error:
                    consecutive_connection_failures += 1
                    # [FIX] Connection failures use fast retry strategy (service exceptions retry immediately)
                    if consecutive_connection_failures <= 3:
                        # First 3 failures: immediate retry (service exceptions are usually temporary)
                        delay = 0.1  # Retry immediately after 100ms
                    elif consecutive_connection_failures <= 6:
                        # 4-6 failures: short delay retry
                        delay = 1.0
                    elif consecutive_connection_failures <= 10:
                        # 7-10 failures: medium delay
                        delay = 3.0
                    else:
                        # Continuous failures: use exponential backoff with 15s cap
                        base_delay = min(5.0 * (consecutive_connection_failures - 10), 15.0)
                        delay = base_delay + self._calculate_backoff_delay(attempt - 10)
                    
                    logger.error(f"[CONNECT] {operation_name} attempt {attempt} connection failure (consecutive {consecutive_connection_failures}): {error_msg[:100]}")
                    logger.warning(f"[RETRY] Retrying after {delay:.1f}s (connection failure, fast retry)")
                    
                elif is_rate_limit_error:
                    consecutive_connection_failures = 0
                    delay = 30.0 + self._calculate_backoff_delay(attempt)  # Rate limit wait 30s
                    logger.warning(f"[LIMIT] {operation_name} attempt {attempt} rate limit encountered: {error_msg[:100]}")
                    logger.warning(f"[RETRY] Retrying after {delay:.1f}s (rate limit, extended wait)")
                    
                elif is_server_error:
                    consecutive_connection_failures = 0
                    # [FIX] Server errors also use fast retry (5xx errors are usually temporary)
                    if attempt <= 3:
                        delay = 0.5  # First 3 fast retries
                    else:
                        delay = 5.0 + self._calculate_backoff_delay(attempt - 3)
                    logger.warning(f"[FIRE] {operation_name} attempt {attempt} server error: {error_msg[:100]}")
                    logger.warning(f"[RETRY] Retrying after {delay:.1f}s (server error, fast retry)")
                    
                elif is_timeout_error:
                    consecutive_connection_failures = 0
                    # [FIX] Timeout errors also use fast retry
                    if attempt <= 3:
                        delay = 0.5  # First 3 fast retries
                    else:
                        delay = 2.0 + self._calculate_backoff_delay(attempt - 3)
                    logger.warning(f"[TIMEOUT]  {operation_name} attempt {attempt} timeout: {error_msg[:100]}")
                    logger.warning(f"[RETRY] Retrying after {delay:.1f}s (timeout, fast retry)")
                    
                else:
                    # Other errors
                    consecutive_connection_failures = 0
                    delay = self._calculate_backoff_delay(attempt)
                    logger.warning(f"[WARN]  {operation_name} attempt {attempt} failed: {error_msg[:100]}")
                    logger.warning(f"[RETRY] Retrying after {delay:.1f}s")
                
                await asyncio.sleep(delay)
                continue
    
    def _calculate_backoff_delay(self, attempt: int) -> float:
        """Calculate exponential backoff delay time"""
        # Exponential backoff: base_delay * (backoff_factor ^ attempt)
        delay = self.base_delay * (self.backoff_factor ** attempt)
        
        # Limit maximum delay
        delay = min(delay, self.max_delay)
        
        # Add random jitter to avoid thundering herd effect
        if self.jitter:
            jitter_range = delay * 0.1  # 10% jitter
            delay += random.uniform(-jitter_range, jitter_range)
            delay = max(0.1, delay)  # Ensure delay is not negative
        
        return delay

    def get_last_prompt(self) -> Optional[str]:
        return self._last_prompt

    def _append_prompt_log(self, level: str, prompt: str, input_excerpt: str) -> None:
        """Append the final concatenated prompt to local log file (JSONL).
        - Use multi-line, well-indented JSON for easy human reading
        - Add blank lines between records as separators
        """
        try:
            os.makedirs("logs/tests", exist_ok=True)
            # Record different levels to different files
            file_map = {
                "L1": "prompts_l1.jsonl",
                "L2": "prompts_l2.jsonl",
                "L3": "prompts_l3.jsonl",
                "L4": "prompts_l4.jsonl",
                "L5": "prompts_l5.jsonl",
            }
            log_path = os.path.join("logs/tests", file_map.get(level, "prompts_misc.jsonl"))
            record = {
                "time": datetime.now().isoformat(),
                "level": level,
                "input_excerpt": input_excerpt,
                "prompt": prompt,
            }
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, indent=2))
                f.write("\n\n")
        except Exception:
            # Log failure does not affect main process
            pass



    # Unified L1 content generation API, handling both first-time and progressive generation
    async def generate_l1_content(self, new_dialogue: str, previous_content: Optional[str] = None) -> str:
        """
        Generate L1 memory content, unified handling for first-time and progressive generation
        
        Args:
            new_dialogue (str): New dialogue content
            previous_content (Optional[str]): Historical memory content, None for first-time generation
            
        Returns:
            str: Generated L1 content
        """
        # Unified use of l1_fragment_summary prompt
        prompt_key = "l1_fragment_summary"
        
        # Get prompt template
        prompt_template = self.prompt_manager.get_prompt(prompt_key)
        if not prompt_template:
            error_msg = f"Cannot get L1 content prompt: {prompt_key}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Format prompt
        # Unified formatting logic: previous_summary is empty string for first generation, historical content for progressive
        formatted_prompt = prompt_template.format(
            previous_summary=previous_content or "",
            new_dialogue=new_dialogue
        )
        from timem.memory.l1_optimization import apply_l1_policy
        formatted_prompt = apply_l1_policy(formatted_prompt, getattr(prompt_template, "template", None))
        
        # Record final prompt for testing
        self._last_prompt = formatted_prompt
        # Local persistence for manual inspection
        self._append_prompt_log("L1", formatted_prompt, input_excerpt=str(new_dialogue)[:120])
        
        # Distinguish log information
        generation_type = "first-time" if previous_content is None else "progressive"
        logger.debug(f"Generated L1 {generation_type} content prompt:\n{formatted_prompt}")

        # Use new exponential backoff retry mechanism
        async def _generate_l1():
            # [FIX] Use streaming generation to prevent timeout
            logger.info(f"[STREAM] Using streaming generation to prevent timeout...")
            content_parts = []
            first_token_received = False
            last_token_time = None
            generation_timeout = self.layer_timeouts.get("l1", 180)  # Get L1 generation timeout
            
            # Convert prompt to Message format for chat_stream
            from llm.base_llm import Message, MessageRole
            messages = [Message(role=MessageRole.USER, content=formatted_prompt)]
            
            stream_start_time = asyncio.get_event_loop().time()
            last_token_time = stream_start_time
            token_interval_timeout = 30  # Token interval timeout (30s)
            
            # [NEW] Pass metadata for Prompt collection
            metadata = {"memory_level": "L1", "trigger_type": "real-time generation"}
            async for chunk in self.llm.chat_stream(messages, metadata=metadata):
                current_time = asyncio.get_event_loop().time()
                
                if chunk:  # Ignore empty chunks
                    if not first_token_received:
                        first_token_received = True
                        logger.info(f"[OK] First token received, starting streaming generation...")
                        last_token_time = current_time
                    else:
                        # Check token interval timeout
                        token_interval = current_time - last_token_time
                        if token_interval > token_interval_timeout:
                            raise asyncio.TimeoutError(f"Token interval timeout: {token_interval:.1f}s > {token_interval_timeout}s without receiving new token")
                        last_token_time = current_time
                    
                    content_parts.append(chunk)
                else:
                    # Empty chunk, check timeout
                    if not first_token_received:
                        elapsed = current_time - stream_start_time
                        if elapsed > token_interval_timeout:
                            raise asyncio.TimeoutError(f"First token reception timeout: {elapsed:.1f}s > {token_interval_timeout}s")
                    else:
                        token_interval = current_time - last_token_time
                        if token_interval > token_interval_timeout:
                            raise asyncio.TimeoutError(f"Token interval timeout: {token_interval:.1f}s > {token_interval_timeout}s without receiving new token")
            
            content_raw = "".join(content_parts)
            
            # [OK] Validate generated content validity
            if not content_raw or len(content_raw.strip()) == 0:
                error_msg = f"L1 {generation_type} content generation failed: returned content is empty"
                logger.error(error_msg)
                raise ValueError(error_msg)  # Throw exception to trigger retry
            
            if len(content_raw.strip()) < 10:  # Content too short, possibly invalid
                error_msg = f"L1 {generation_type} content generation failed: returned content too short ({len(content_raw)} characters)"
                logger.warning(error_msg)
                raise ValueError(error_msg)  # Throw exception to trigger retry
            
            logger.info(f"[OK] L1 {generation_type} content streaming generation completed, length: {len(content_raw)} characters")
            
            # Try to parse mock return
            try:
                import json
                data = json.loads(content_raw)
                choices = (data or {}).get("data", {}).get("choices", [])
                text = choices[0].get("content") if choices else content_raw
                # Try to parse inner JSON
                try:
                    inner = json.loads(text)
                    final_content = inner.get("content") or inner.get("summary") or text
                except Exception:
                    final_content = text
            except Exception:
                final_content = content_raw
            
            # Validate final content again
            if not final_content or len(final_content.strip()) < 10:
                error_msg = f"L1 {generation_type} content invalid after parsing"
                logger.error(error_msg)
                raise ValueError(error_msg)  # Throw exception to trigger retry
            
            return final_content

        return await self._retry_with_exponential_backoff(_generate_l1, "l1", f"L1 {generation_type} content generation")

    async def generate_l2_content(self, child_contents: List[str], previous_content: Optional[str] = None) -> str:
        """
        Generate L2 session-level content, unified handling for first-time and progressive generation
        
        Args:
            child_contents (List[str]): List of L1 memory content
            previous_content (Optional[str]): Historical L2 memory content, None for first-time generation
            
        Returns:
            str: Generated L2 content
        """
        # Add detailed debug information
        print(f"\n[SEARCH] [L2 Memory Generation] Starting L2 content generation, child content count: {len(child_contents)}")
        print(f"[CHART] Historical content status: {'Has historical content' if previous_content else 'No historical content (first-time generation)'}")
        
        # Currently simple processing as L2 summary generation
        # TODO: In the future, implement true progressive generation based on whether previous_content exists
        
        # Correct prompt key name - use the correct key name defined in prompts.yaml
        prompt_key = "l2_session_summary"  # Correct key name, corresponding to L2 session-level memory prompt in prompts.yaml
        print(f"[KEY] Using prompt key name: '{prompt_key}'")
        
        # Get prompt template
        prompt_template = self.prompt_manager.get_prompt(prompt_key)
        print(f"[SEARCH] Prompt retrieval result: {'Success' if prompt_template else 'Failure'}")
        
        if not prompt_template:
            error_msg = f"Cannot get L2 session summary prompt: {prompt_key}"
            print(f"[ERROR] Error: {error_msg}")
            logger.error(error_msg)
            
            # List all available prompt key names
            if hasattr(self.prompt_manager, "_prompts") and isinstance(self.prompt_manager._prompts, dict):
                available_keys = list(self.prompt_manager._prompts.keys())
                print(f"[KEY] Available prompt key names: {available_keys}")
                
            raise ValueError(error_msg)
        else:
            # Prompt template preview
            preview_length = min(100, len(str(prompt_template)))
            print(f"[OK] Prompt template retrieved successfully, length: {len(str(prompt_template))} characters")
            print(f"[DOC] Prompt template preview: {str(prompt_template)[:preview_length]}...")

        # Fragment summaries concatenated chronologically from past to present
        formatted_summaries = "\n".join([f"Fragment {i+1}: {summary}" for i, summary in enumerate(child_contents)])
        formatted_prompt = prompt_template.format(fragment_summaries=formatted_summaries)
        # Record and persist
        self._last_prompt = formatted_prompt
        self._append_prompt_log("L2", formatted_prompt, input_excerpt=formatted_summaries[:120])

        logger.debug(f"Generated L2 session overall content prompt:\n{formatted_prompt}")
        print(f"[NOTE] Final prompt length: {len(formatted_prompt)} characters")
        
        # Check LLM instance
        print(f"[AI] LLM instance type: {type(self.llm).__name__}")
        print(f"[WAIT] Starting LLM call...")
        
        # Use new exponential backoff retry mechanism
        async def _generate_l2():
            # [FIX] Use streaming generation to prevent timeout
            print(f"[STREAM] Using streaming generation to prevent timeout...")
            content_parts = []
            first_token_received = False
            last_token_time = None
            generation_timeout = self.layer_timeouts.get("l2", 180)  # Get L2 generation timeout
            
            # Convert prompt to Message format for chat_stream
            from llm.base_llm import Message, MessageRole
            messages = [Message(role=MessageRole.USER, content=formatted_prompt)]
            
            stream_start_time = asyncio.get_event_loop().time()
            last_token_time = stream_start_time
            token_interval_timeout = 30  # Token interval timeout (30 seconds)
            
            # [NEW] Pass metadata for Prompt collection
            metadata = {"memory_level": "L2", "trigger_type": "session-level generation"}
            async for chunk in self.llm.chat_stream(messages, metadata=metadata):
                current_time = asyncio.get_event_loop().time()
                
                if chunk:  # Ignore empty chunks
                    if not first_token_received:
                        first_token_received = True
                        print(f"[OK] First token received, starting streaming generation...")
                        last_token_time = current_time
                    else:
                        # Check token interval timeout
                        token_interval = current_time - last_token_time
                        if token_interval > token_interval_timeout:
                            raise asyncio.TimeoutError(f"Token interval timeout: {token_interval:.1f}s > {token_interval_timeout}s without receiving new token")
                        last_token_time = current_time
                    
                    content_parts.append(chunk)
                else:
                    # Empty chunk, check timeout
                    if not first_token_received:
                        elapsed = current_time - stream_start_time
                        if elapsed > token_interval_timeout:
                            raise asyncio.TimeoutError(f"First token reception timeout: {elapsed:.1f}s > {token_interval_timeout}s")
                    else:
                        token_interval = current_time - last_token_time
                        if token_interval > token_interval_timeout:
                            raise asyncio.TimeoutError(f"Token interval timeout: {token_interval:.1f}s > {token_interval_timeout}s without receiving new token")
            
            content = "".join(content_parts)
            
            # [OK] Validate generated content validity
            if not content or len(content.strip()) == 0:
                error_msg = "L2 session content generation failed: returned content is empty"
                print(f"[ERROR] {error_msg}")
                logger.error(error_msg)
                raise ValueError(error_msg)  # Throw exception to trigger retry
            
            if len(content.strip()) < 20:  # L2 content should be longer
                error_msg = f"L2 session content generation failed: returned content too short ({len(content)} characters)"
                print(f"[ERROR] {error_msg}")
                logger.warning(error_msg)
                raise ValueError(error_msg)  # Throw exception to trigger retry
            
            # Check return result
            content_length = len(content)
            print(f"[OK] LLM streaming generation completed, returned content length: {content_length} characters")
            
            # Content preview
            preview_length = min(150, content_length)
            print(f"[DOC] Generated content preview: {content[:preview_length]}...")
            
            # Detect if it's hardcoded fallback content
            if "Session summary -" in content or "Session summary:" in content:
                print(f"[WARN] Warning: Suspected hardcoded fallback text! This may mean actual content generation failed")
                error_msg = "L2 session content suspected hardcoded fallback"
                logger.warning(error_msg)
                raise ValueError(error_msg)  # Throw exception to trigger retry
                
            logger.info(f"Successfully generated L2 session overall content, length: {content_length} characters")
            return content

        return await self._retry_with_exponential_backoff(_generate_l2, "l2", "L2 session content generation")

    def _is_generic_content(self, content: str) -> bool:
        """
        Check if generated content is too generic (template text without specific information)

        Args:
            content: Generated content

        Returns:
            True if content is too generic, False otherwise
        """
        if not content or len(content.strip()) < 50:
            return True

        # 检测通用模板模式
        generic_patterns = [
            r"In this session,? a user",  # "In this session, a user..."
            r"engaged in (a )?conversation",  # "engaged in conversation"
            r"interaction[s]? revolved around",  # "interaction revolved around"
            r"specific queries and responses",  # "specific queries and responses"
            r"details (of )?the (exchanges|conversation)",  # "details of the exchanges"
            r"were not provided",  # "were not provided"
            r"Based on the fragment",  # "Based on the fragment..."
            r"the user.*and.*assistant.*discussed",  # "the user and assistant discussed..."
            r"various topics",  # "various topics"
            r"relevant to.*interests",  # "relevant to...interests"
            r"posed questions",  # "posed questions"
            r"provided detailed responses",  # "provided detailed responses"
            r"While the specific content",  # "While the specific content..."
            r"cannot be determined",  # "cannot be determined"
        ]

        content_lower = content.lower()
        match_count = 0
        for pattern in generic_patterns:
            if re.search(pattern, content_lower):
                match_count += 1

        # 如果匹配多个通用模式，认为是通用内容
        if match_count >= 2:
            return True

        # 检查内容是否太短或信息密度太低
        word_count = len(content.split())
        if word_count < 30:
            return True

        # 检查是否有具体信息（人名、技术术语、数字等）
        has_specific_info = bool(re.search(r'\b(python|java|javascript|go|rust|tensorflow|pytorch|django|flask)\b', content_lower))
        has_numbers = bool(re.search(r'\d+', content))
        has_dates = bool(re.search(r'\b\d{4}[-/]\d{1,2}[-/]\d{1,2}\b', content))

        # 如果没有任何具体信息，且内容较短，认为是通用内容
        if not has_specific_info and not has_numbers and not has_dates and word_count < 100:
            return True

        return False

    def _get_enhanced_l2_prompt(self, child_contents: str, previous_content: Optional[str] = None) -> str:
        """
        Get enhanced L2 prompt with more specific instructions for keyword preservation

        Args:
            child_contents: Child memory contents
            previous_content: Previous L2 content

        Returns:
            Enhanced prompt text
        """
        enhanced_prompt = """You are generating a session summary for an AI memory system.

IMPORTANT REQUIREMENTS:
1. PRESERVE SPECIFIC KEYWORDS from the original dialogue:
   - Programming languages: Python, Java, JavaScript, Go, Rust, etc.
   - Technology terms: machine learning, deep learning, neural network, etc.
   - Tool names: TensorFlow, PyTorch, Django, Flask, etc.
   - Domain-specific terminology
   - Proper nouns: company names, product names, etc.

2. DO NOT paraphrase specific terms into generic terms:
   - "Python" should remain "Python", not "programming language"
   - "TensorFlow" should remain "TensorFlow", not "framework"
   - Keep technical terms in their original form

3. Include specific examples and details from the dialogue.

Input fragments:
{fragment_summaries}

Please generate a summary that preserves the original keywords and specific information.

Session Summary:""".format(fragment_summaries=child_contents)

        return enhanced_prompt

    def _add_content_warning(self, generated_text: str, valid_contents: List[str]) -> str:
        """
        Add content warning when generated content is detected as too generic

        Args:
            generated_text: Originally generated content
            valid_contents: Original valid contents used for generation

        Returns:
            Content with added warning note
        """
        # 提取原始内容中的关键术语
        keywords = set()
        for content in valid_contents:
            # 提取技术术语
            tech_terms = re.findall(r'\b(python|java|javascript|go|rust|tensorflow|pytorch|django|flask|vs code|pycharm|leetcode|sql|react|node\.?js)\b', content.lower())
            keywords.update(tech_terms)

        # 如果有关键词，添加到内容开头
        if keywords:
            keyword_list = ', '.join(sorted(keywords))
            warning_note = f"[Note: This session involves discussions about {keyword_list}. The summary should reflect these topics.]\n\n"
            return warning_note + generated_text

        return generated_text
            


    async def generate_l3_content(self, child_contents: List[str], previous_content: Optional[str] = None, date: Optional[str] = None) -> str:
        """
        Generate L3 daily-level content, unified handling for first-time and progressive generation
        
        Args:
            child_contents (List[str]): List of L2 memory content
            previous_content (Optional[str]): Historical L3 memory content, None for first-time generation
            date (Optional[str]): Current date, format YYYY-MM-DD
            
        Returns:
            str: Generated L3 content
        """
        # Add detailed debug information
        print(f"\n[SEARCH] [L3 Memory Generation] Starting L3 content generation, child content count: {len(child_contents)}")
        print(f"[CHART] Historical content status: {'Has historical content' if previous_content else 'No historical content (first-time generation)'}")
        
        # Currently simple processing as L3 summary generation
        # TODO: In the future, implement true progressive generation based on whether previous_content exists
        prompt_key = "l3_daily_summary"
        print(f"[KEY] Using prompt key name: '{prompt_key}'")
        
        # Get prompt template
        prompt_template = self.prompt_manager.get_prompt(prompt_key)
        print(f"[SEARCH] Prompt retrieval result: {'Success' if prompt_template else 'Failure'}")
        
        if not prompt_template:
            error_msg = f"Cannot get L3 daily content prompt: {prompt_key}"
            print(f"[ERROR] Error: {error_msg}")
            logger.error(error_msg)
            
            # List all available prompt key names
            if hasattr(self.prompt_manager, "_prompts") and isinstance(self.prompt_manager._prompts, dict):
                available_keys = list(self.prompt_manager._prompts.keys())
                print(f"[KEY] Available prompt key names: {available_keys}")
                
            raise ValueError(error_msg)
        else:
            # Prompt template preview
            preview_length = min(100, len(str(prompt_template)))
            print(f"[OK] Prompt template retrieved successfully, length: {len(str(prompt_template))} characters")
            print(f"[DOC] Prompt template preview: {str(prompt_template)[:preview_length]}...")
        
        # Format session summaries
        formatted_summaries = "\n".join([f"Session {i+1}: {summary}" for i, summary in enumerate(child_contents)])
        
        # Format historical daily memories (if any)
        previous_daily_memories = previous_content if previous_content else "No historical daily memories."
        
        # Get date information (if not provided, use current date)
        if not date:
            from datetime import datetime
            date = datetime.now().strftime("%Y-%m-%d")
        
        # Format prompt - use new parameters
        formatted_prompt = prompt_template.format(
            date=date,
            session_summaries=formatted_summaries,
            previous_daily_memories=previous_daily_memories
        )
        
        # Record and persist
        self._last_prompt = formatted_prompt
        self._append_prompt_log("L3", formatted_prompt, input_excerpt=formatted_summaries[:120])
        
        logger.debug(f"Generated L3 daily content prompt:\n{formatted_prompt}")
        print(f"[NOTE] Final prompt length: {len(formatted_prompt)} characters")
        
        # Check LLM instance
        print(f"[AI] LLM instance type: {type(self.llm).__name__}")
        print(f"[WAIT] Starting LLM call...")

        # Use new exponential backoff retry mechanism
        async def _generate_l3():
            # [FIX] Use streaming generation to prevent timeout
            print(f"[STREAM] Using streaming generation to prevent timeout...")
            content_parts = []
            first_token_received = False
            last_token_time = None
            generation_timeout = self.layer_timeouts.get("l3", 180)  # Get L3 generation timeout
            
            # Convert prompt to Message format for chat_stream
            from llm.base_llm import Message, MessageRole
            messages = [Message(role=MessageRole.USER, content=formatted_prompt)]
            
            stream_start_time = asyncio.get_event_loop().time()
            last_token_time = stream_start_time
            token_interval_timeout = 30  # Token interval timeout (30 seconds)
            
            # [NEW] Pass metadata for Prompt collection
            metadata = {"memory_level": "L3", "trigger_type": "daily report generation"}
            async for chunk in self.llm.chat_stream(messages, metadata=metadata):
                current_time = asyncio.get_event_loop().time()
                
                if chunk:  # Ignore empty chunks
                    if not first_token_received:
                        first_token_received = True
                        print(f"[OK] First token received, starting streaming generation...")
                        last_token_time = current_time
                    else:
                        # Check token interval timeout
                        token_interval = current_time - last_token_time
                        if token_interval > token_interval_timeout:
                            raise asyncio.TimeoutError(f"Token interval timeout: {token_interval:.1f}s > {token_interval_timeout}s without receiving new token")
                        last_token_time = current_time
                    
                    content_parts.append(chunk)
                else:
                    # Empty chunk, check timeout
                    if not first_token_received:
                        elapsed = current_time - stream_start_time
                        if elapsed > token_interval_timeout:
                            raise asyncio.TimeoutError(f"First token reception timeout: {elapsed:.1f}s > {token_interval_timeout}s")
                    else:
                        token_interval = current_time - last_token_time
                        if token_interval > token_interval_timeout:
                            raise asyncio.TimeoutError(f"Token interval timeout: {token_interval:.1f}s > {token_interval_timeout}s without receiving new token")
            
            content = "".join(content_parts)
            
            # [OK] Validate generated content validity
            if not content or len(content.strip()) == 0:
                error_msg = "L3 daily report content generation failed: returned content is empty"
                print(f"[ERROR] {error_msg}")
                logger.error(error_msg)
                raise ValueError(error_msg)  # Throw exception to trigger retry
            
            if len(content.strip()) < 30:  # L3 content should be longer
                error_msg = f"L3 daily report content generation failed: returned content too short ({len(content)} characters)"
                print(f"[ERROR] {error_msg}")
                logger.warning(error_msg)
                raise ValueError(error_msg)  # Throw exception to trigger retry
            
            # Check return result
            content_length = len(content)
            print(f"[OK] LLM streaming generation completed, returned content length: {content_length} characters")
            
            # Content preview
            preview_length = min(150, content_length)
            print(f"[DOC] Generated content preview: {content[:preview_length]}...")
            
            # Detect if it's hardcoded fallback content
            if "Daily Report Summary:" in content or "Daily Summary -" in content:
                print(f"[WARN] Warning: Suspected hardcoded fallback text! This may mean actual content generation failed")
                error_msg = "L3 daily report content suspected hardcoded fallback"
                logger.warning(error_msg)
                raise ValueError(error_msg)  # Throw exception to trigger retry
                
            logger.info(f"Successfully generated L3 daily report content, length: {content_length} characters")
            return content

        return await self._retry_with_exponential_backoff(_generate_l3, "l3", "L3 daily report content generation")
            


    async def generate_l4_content(self, child_contents: List[str], previous_content: Optional[str] = None, 
                                 year: Optional[int] = None, week_number: Optional[int] = None,
                                 week_start: Optional[str] = None, week_end: Optional[str] = None) -> str:
        """
        Generate L4 weekly-level content, unified handling for first-time and progressive generation
        
        Args:
            child_contents (List[str]): List of L3 memory content
            previous_content (Optional[str]): Historical L4 memory content, None for first-time generation
            year (Optional[int]): Year
            week_number (Optional[int]): Week number
            week_start (Optional[str]): Week start date, format YYYY-MM-DD
            week_end (Optional[str]): Week end date, format YYYY-MM-DD
            
        Returns:
            str: Generated L4 content
        """
        # Currently simple processing as L4 summary generation
        # TODO: In the future, implement true progressive generation based on whether previous_content exists
        prompt_key = "l4_weekly_summary"
        
        # Get prompt template
        prompt_template = self.prompt_manager.get_prompt(prompt_key)
        if not prompt_template:
            error_msg = f"Cannot get L4 weekly report content prompt: {prompt_key}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Format daily summaries
        formatted_summaries = "\n".join([f"Daily Report {i+1}: {summary}" for i, summary in enumerate(child_contents)])
        
        # Format historical weekly memories (if any)
        previous_weekly_memories = previous_content if previous_content else "No historical weekly report memories."
        
        # Get time information (if not provided, use current time)
        if not year or not week_number:
            from datetime import datetime
            now = datetime.now()
            year = now.isocalendar()[0]
            week_number = now.isocalendar()[1]
        if not week_start or not week_end:
            from datetime import datetime, timedelta
            # If week start/end dates are not provided, calculate using year and week number
            first_day_of_year = datetime(year, 1, 1)
            days_to_monday = (week_number - 1) * 7 - first_day_of_year.weekday()
            week_start_date = first_day_of_year + timedelta(days=days_to_monday)
            week_end_date = week_start_date + timedelta(days=6)
            week_start = week_start_date.strftime("%Y-%m-%d")
            week_end = week_end_date.strftime("%Y-%m-%d")
        
        # Format prompt - use new parameters
        formatted_prompt = prompt_template.format(
            year=year,
            week_number=week_number,
            week_start=week_start,
            week_end=week_end,
            daily_summaries=formatted_summaries,
            previous_weekly_memories=previous_weekly_memories
        )
        
        # Record and persist
        self._last_prompt = formatted_prompt
        self._append_prompt_log("L4", formatted_prompt, input_excerpt=formatted_summaries[:120])
        
        logger.debug(f"Generated L4 weekly report content prompt:\n{formatted_prompt}")

        # Use new exponential backoff retry mechanism
        async def _generate_l4():
            # [FIX] Use streaming generation to prevent timeout
            logger.info("[STREAM] Using streaming generation to prevent timeout...")
            content_parts = []
            first_token_received = False
            last_token_time = None
            generation_timeout = self.layer_timeouts.get("l4", 180)  # Get L4 generation timeout
            
            # Convert prompt to Message format for chat_stream
            from llm.base_llm import Message, MessageRole
            messages = [Message(role=MessageRole.USER, content=formatted_prompt)]
            
            stream_start_time = asyncio.get_event_loop().time()
            last_token_time = stream_start_time
            token_interval_timeout = 30  # Token interval timeout (30 seconds)
            
            # [NEW] Pass metadata for Prompt collection
            metadata = {"memory_level": "L4", "trigger_type": "weekly report generation"}
            async for chunk in self.llm.chat_stream(messages, metadata=metadata):
                current_time = asyncio.get_event_loop().time()
                
                if chunk:  # Ignore empty chunks
                    if not first_token_received:
                        first_token_received = True
                        logger.info(f"[OK] First token received, starting streaming generation...")
                        last_token_time = current_time
                    else:
                        # Check token interval timeout
                        token_interval = current_time - last_token_time
                        if token_interval > token_interval_timeout:
                            raise asyncio.TimeoutError(f"Token interval timeout: {token_interval:.1f}s > {token_interval_timeout}s without receiving new token")
                        last_token_time = current_time
                    
                    content_parts.append(chunk)
                else:
                    # Empty chunk, check timeout
                    if not first_token_received:
                        elapsed = current_time - stream_start_time
                        if elapsed > token_interval_timeout:
                            raise asyncio.TimeoutError(f"First token reception timeout: {elapsed:.1f}s > {token_interval_timeout}s")
                    else:
                        token_interval = current_time - last_token_time
                        if token_interval > token_interval_timeout:
                            raise asyncio.TimeoutError(f"Token interval timeout: {token_interval:.1f}s > {token_interval_timeout}s without receiving new token")
            
            content = "".join(content_parts)
            
            # [OK] Validate generated content validity
            if not content or len(content.strip()) == 0:
                error_msg = "L4 weekly report content generation failed: returned content is empty"
                logger.error(error_msg)
                raise ValueError(error_msg)  # Throw exception to trigger retry
            
            if len(content.strip()) < 40:  # L4 content should be longer
                error_msg = f"L4 weekly report content generation failed: returned content too short ({len(content)} characters)"
                logger.warning(error_msg)
                raise ValueError(error_msg)  # Throw exception to trigger retry
            
            logger.info(f"[OK] L4 weekly report content streaming generation completed, length: {len(content)} characters")
            return content

        return await self._retry_with_exponential_backoff(_generate_l4, "l4", "L4 weekly report content generation")
            


    async def generate_l5_content(self, child_contents: List[str], previous_content: Optional[str] = None,
                                 year: Optional[int] = None, month: Optional[int] = None,
                                 month_start: Optional[str] = None, month_end: Optional[str] = None) -> str:
        """
        Generate L5 monthly-level/high-level content, unified handling for first-time and progressive generation
        
        Args:
            child_contents (List[str]): List of L4 memory content
            previous_content (Optional[str]): Historical L5 memory content, None for first-time generation
            year (Optional[int]): Year
            month (Optional[int]): Month
            month_start (Optional[str]): Month start date, format YYYY-MM-DD
            month_end (Optional[str]): Month end date, format YYYY-MM-DD
            
        Returns:
            str: Generated L5 content
        """
        # Currently simple processing as L5 summary generation
        # TODO: In the future, implement true progressive generation based on whether previous_content exists
        prompt_key = "l5_high_level_summary"
        
        # Get prompt template
        prompt_template = self.prompt_manager.get_prompt(prompt_key)
        if not prompt_template:
            error_msg = f"Cannot get L5 monthly/high-level content prompt: {prompt_key}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Format weekly memory list
        formatted_memories = "\n".join([f"Weekly Report {i+1}: {memory}" for i, memory in enumerate(child_contents)])
        
        # Format historical monthly memories (if any)
        previous_monthly_memories = previous_content if previous_content else "No historical monthly report memories."
        
        # Get month information (if not provided, use current month)
        if not year or not month:
            from datetime import datetime
            now = datetime.now()
            year = now.year
            month = now.month
        
        # Calculate month name and start/end dates
        import calendar
        from datetime import datetime
        if not month_start or not month_end:
            # Calculate first and last day of month
            first_day = datetime(year, month, 1)
            last_day_num = calendar.monthrange(year, month)[1]
            last_day = datetime(year, month, last_day_num)
            month_start = first_day.strftime("%Y-%m-%d")
            month_end = last_day.strftime("%Y-%m-%d")
        
        # Month names (English)
        month_names = ["January", "February", "March", "April", "May", "June",
                      "July", "August", "September", "October", "November", "December"]
        month_name = month_names[month - 1]
        
        # Format prompt - use new parameters
        formatted_prompt = prompt_template.format(
            year=year,
            month=month,
            month_name=month_name,
            month_start=month_start,
            month_end=month_end,
            expert_memories=formatted_memories,
            previous_monthly_memories=previous_monthly_memories
        )
        
        # Record and persist
        self._last_prompt = formatted_prompt
        self._append_prompt_log("L5", formatted_prompt, input_excerpt=formatted_memories[:120])
        
        logger.debug(f"Generated L5 monthly/high-level content prompt:\n{formatted_prompt}")

        # Use new exponential backoff retry mechanism
        async def _generate_l5():
            # [FIX] Use streaming generation to prevent timeout
            logger.info("[STREAM] Using streaming generation to prevent timeout...")
            content_parts = []
            first_token_received = False
            token_interval_timeout = 30  # Token interval timeout (30 seconds)
            
            # Convert prompt to Message format for chat_stream
            from llm.base_llm import Message, MessageRole
            messages = [Message(role=MessageRole.USER, content=formatted_prompt)]
            
            stream_start_time = asyncio.get_event_loop().time()
            last_token_time = stream_start_time  # Initialize to stream start time
            
            # [NEW] Pass metadata for Prompt collection
            metadata = {"memory_level": "L5", "trigger_type": "monthly report generation"}
            async for chunk in self.llm.chat_stream(messages, metadata=metadata):
                current_time = asyncio.get_event_loop().time()
                
                if chunk:  # Ignore empty chunks
                    if not first_token_received:
                        first_token_received = True
                        logger.info(f"[OK] First token received, starting streaming generation...")
                        last_token_time = current_time  # Reset to first token time
                    else:
                        # Check token interval timeout
                        token_interval = current_time - last_token_time
                        if token_interval > token_interval_timeout:
                            raise asyncio.TimeoutError(f"Token interval timeout: {token_interval:.1f}s > {token_interval_timeout}s without receiving new token")
                        last_token_time = current_time  # Update last token time
                    
                    content_parts.append(chunk)
                else:
                    # Empty chunk, check timeout
                    if not first_token_received:
                        # First token timeout check
                        elapsed = current_time - stream_start_time
                        if elapsed > token_interval_timeout:
                            raise asyncio.TimeoutError(f"First token reception timeout: {elapsed:.1f}s > {token_interval_timeout}s")
                    else:
                        # Token interval timeout check
                        token_interval = current_time - last_token_time
                        if token_interval > token_interval_timeout:
                            raise asyncio.TimeoutError(f"Token interval timeout: {token_interval:.1f}s > {token_interval_timeout}s without receiving new token")
            
            # If loop ends but no first token received, connection timeout
            if not first_token_received:
                elapsed = asyncio.get_event_loop().time() - stream_start_time
                raise asyncio.TimeoutError(f"First token reception timeout: {elapsed:.1f}s > {token_interval_timeout}s")
            
            content = "".join(content_parts)
            
            # [OK] Validate generated content validity
            if not content or len(content.strip()) == 0:
                error_msg = "L5 monthly report content generation failed: returned content is empty"
                logger.error(error_msg)
                raise ValueError(error_msg)  # Throw exception to trigger retry
            
            if len(content.strip()) < 50:  # L5 content should be longer
                error_msg = f"L5 monthly report content generation failed: returned content too short ({len(content)} characters)"
                logger.warning(error_msg)
                raise ValueError(error_msg)  # Throw exception to trigger retry
            
            logger.info(f"[OK] L5 monthly report content streaming generation completed, length: {len(content)} characters")
            return content

        return await self._retry_with_exponential_backoff_for_streaming(_generate_l5, "l5", "L5 monthly report content generation")
    
    
    # ========================================
    # [INFO] Streaming generation methods (for low-latency experience)
    # ========================================
    
    async def generate_l1_content_stream(
        self, 
        new_dialogue: str, 
        previous_content: Optional[str] = None,
        progress_callback: Optional[callable] = None
    ):
        """
        Stream generation of L1 memory content
        
        Args:
            new_dialogue: New dialogue content
            previous_content: Historical memory content
            progress_callback: Progress callback function, receives (delta: str, full_content: str) parameters
            
        Yields:
            str: Streamed content fragments
        """
        prompt_key = "l1_fragment_summary"
        prompt_template = self.prompt_manager.get_prompt(prompt_key)
        
        if not prompt_template:
            error_msg = f"Cannot get L1 content prompt: {prompt_key}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        formatted_prompt = prompt_template.format(
            previous_summary=previous_content or "",
            new_dialogue=new_dialogue
        )
        from timem.memory.l1_optimization import apply_l1_policy
        formatted_prompt = apply_l1_policy(formatted_prompt, getattr(prompt_template, "template", None))
        
        self._last_prompt = formatted_prompt
        self._append_prompt_log("L1", formatted_prompt, input_excerpt=str(new_dialogue)[:120])
        
        generation_type = "first-time" if previous_content is None else "progressive"
        logger.info(f"Starting streaming generation of L1 {generation_type} content")
        
        full_content = ""
        
        try:
            # Use LLM's streaming interface
            from llm.base_llm import Message, MessageRole
            messages = [Message(role=MessageRole.USER, content=formatted_prompt)]
            
            async for delta in self.llm.chat_stream(messages):
                full_content += delta
                
                # Call progress callback
                if progress_callback:
                    await progress_callback(delta, full_content)
                
                yield delta
            
            logger.info(f"Streaming generation of L1 {generation_type} content completed, total length: {len(full_content)} characters")
            
        except Exception as e:
            logger.error(f"Failed to stream generate L1 content: {e}", exc_info=True)
            raise
    
    async def generate_l2_content_stream(
        self,
        child_contents: List[str],
        previous_content: Optional[str] = None,
        progress_callback: Optional[callable] = None
    ):
        """
        Stream generation of L2 memory content
        
        Args:
            child_contents: List of L1 memory content
            previous_content: Historical L2 memory content
            progress_callback: Progress callback function, receives (delta: str, full_content: str) parameters
            
        Yields:
            str: Streamed content fragments
        """
        prompt_key = "l2_session_summary"
        prompt_template = self.prompt_manager.get_prompt(prompt_key)
        
        if not prompt_template:
            error_msg = f"Cannot get L2 session summary prompt: {prompt_key}"
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        formatted_summaries = "\n".join([f"Fragment {i+1}: {summary}" for i, summary in enumerate(child_contents)])
        formatted_prompt = prompt_template.format(fragment_summaries=formatted_summaries)
        
        self._last_prompt = formatted_prompt
        self._append_prompt_log("L2", formatted_prompt, input_excerpt=formatted_summaries[:120])
        
        logger.info("Starting streaming generation of L2 session content")
        
        full_content = ""
        
        try:
            # Use LLM's streaming interface
            from llm.base_llm import Message, MessageRole
            messages = [Message(role=MessageRole.USER, content=formatted_prompt)]
            
            async for delta in self.llm.chat_stream(messages):
                full_content += delta
                
                # Call progress callback
                if progress_callback:
                    await progress_callback(delta, full_content)
                
                yield delta
            
            logger.info(f"Streaming generation of L2 session content completed, total length: {len(full_content)} characters")
            
        except Exception as e:
            logger.error(f"Failed to stream generate L2 content: {e}", exc_info=True)
            raise
            
