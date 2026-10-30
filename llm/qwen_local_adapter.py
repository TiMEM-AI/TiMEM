"""
Qwen3-8B Local Model Adapter
Supports using local Qwen3-8B model for conversation and text generation
"""

import os
import time
import asyncio
from typing import List, Dict, Any, Optional, AsyncIterator
import numpy as np

from .base_llm import (
    BaseLLM, Message, MessageRole, ChatResponse, EmbeddingResponse, ModelConfig,
    ModelType, handle_llm_errors
)
from timem.utils.config_manager import get_llm_config
from timem.utils.logging import get_logger

logger = get_logger(__name__)

class QwenLocalAdapter(BaseLLM):
    """Qwen3-8B Local Model Adapter"""
    
    def __init__(self, config: Optional[ModelConfig] = None):
        # Get configuration
        llm_config = get_llm_config().get("providers", {}).get("qwen_local", {})
        
        if config is None:
            config = ModelConfig(
                model_name=llm_config.get("model", "Qwen3-8B"),
                temperature=llm_config.get("temperature", 0.7),
                max_tokens=llm_config.get("max_tokens", 2048)
            )
        
        super().__init__(config)
        
        # Model path configuration
        self.model_path = llm_config.get("model_path", "./models/Qwen3-8B")
        self.device = llm_config.get("device", "auto")
        self.torch_dtype = llm_config.get("torch_dtype", "auto")
        self.max_length = llm_config.get("max_length", 4096)
        
        # Quantization configuration
        self.quantization_config = llm_config.get("quantization", {})
        self.enable_quantization = self.quantization_config.get("enabled", True)
        self.quantization_method = self.quantization_config.get("method", "bitsandbytes")  # bitsandbytes, dynamic, static
        self.load_in_8bit = self.quantization_config.get("load_in_8bit", True)
        self.load_in_4bit = self.quantization_config.get("load_in_4bit", False)
        
        # Load model and tokenizer
        self.model = None
        self.tokenizer = None
        self.model_loaded = False
        
        # Supported models
        self.supported_models = ["Qwen3-8B", "qwen3-8b"]
        
        # Initialize model
        self._initialize_model()
        
        logger.info(f"Qwen3-8B local adapter initialized successfully, model path: {self.model_path}")
    
    def _initialize_model(self):
        """Initialize model and tokenizer"""
        try:
            # Lazy import: torch/transformers only for local qwen_local provider
            try:
                import torch
                from transformers import AutoTokenizer, AutoModelForCausalLM
            except ImportError as e:
                raise ImportError(
                    "torch and transformers are required for qwen_local; "
                    "install optional local-model deps or use an API LLM provider"
                ) from e

            if not os.path.exists(self.model_path):
                raise FileNotFoundError(f"Model path does not exist: {self.model_path}")
            
            logger.info(f"Loading Qwen3-8B model from {self.model_path}...")
            
            # Load tokenizer
            self.tokenizer = AutoTokenizer.from_pretrained(
                self.model_path,
                trust_remote_code=True
            )
            
            # Set pad token
            if self.tokenizer.pad_token is None:
                self.tokenizer.pad_token = self.tokenizer.eos_token
            
            # Load model - optimize configuration for faster inference
            device_map = "auto" if self.device == "auto" else self.device
            torch_dtype = torch.float16 if self.torch_dtype == "auto" else getattr(torch, self.torch_dtype)
            
            # Use optimized model loading configuration
            # Check if Flash Attention 2 is supported
            use_flash_attention = False
            if torch.cuda.is_available():
                try:
                    import flash_attn
                    use_flash_attention = True
                    logger.info("Flash Attention 2 detected, using it for better performance")
                except ImportError:
                    logger.warning("Flash Attention 2 not installed, using standard attention mechanism")
            
            # Set quantization configuration
            quantization_config = None
            if self.enable_quantization and torch.cuda.is_available():
                if self.quantization_method == "bitsandbytes":
                    try:
                        from transformers import BitsAndBytesConfig
                        
                        if self.load_in_4bit:
                            # 4-bit quantization configuration (most aggressive compression)
                            quantization_config = BitsAndBytesConfig(
                                load_in_4bit=True,
                                bnb_4bit_use_double_quant=True,  # Double quantization
                                bnb_4bit_quant_type="nf4",      # Use NF4 quantization
                                bnb_4bit_compute_dtype=torch.float16,  # Compute precision
                            )
                            logger.info("4-bit quantization enabled (NF4)")
                        elif self.load_in_8bit:
                            # 8-bit quantization configuration (balanced performance and quality)
                            quantization_config = BitsAndBytesConfig(
                                load_in_8bit=True,
                                llm_int8_threshold=6.0,
                                llm_int8_has_fp16_weight=False,
                            )
                            logger.info("8-bit quantization enabled")
                            
                    except ImportError:
                        logger.warning("bitsandbytes not installed, skipping quantization")
                        
            # Load model
            self.model = AutoModelForCausalLM.from_pretrained(
                self.model_path,
                torch_dtype=torch_dtype,
                device_map=device_map,
                trust_remote_code=True,
                low_cpu_mem_usage=True,
                use_cache=True,  # Enable KV cache
                attn_implementation="flash_attention_2" if use_flash_attention else None,  # Conditionally use Flash Attention 2
                quantization_config=quantization_config,  # Quantization configuration
            )
            
            # Apply dynamic quantization (if not using bitsandbytes quantization)
            if (self.enable_quantization and 
                self.quantization_method == "dynamic" and 
                quantization_config is None):
                try:
                    logger.info("Applying dynamic quantization...")
                    self.model = torch.quantization.quantize_dynamic(
                        self.model,
                        {torch.nn.Linear},  # Quantize linear layers
                        dtype=torch.qint8   # Use 8-bit integers
                    )
                    logger.info("Dynamic quantization applied successfully")
                except Exception as e:
                    logger.warning(f"Dynamic quantization failed: {e}")
            
            # Compile model for faster inference (PyTorch 2.0+)
            if hasattr(torch, 'compile') and torch.cuda.is_available() and quantization_config is None:
                try:
                    self.model = torch.compile(self.model, mode="reduce-overhead")
                    logger.info("Model compiled for faster inference")
                except Exception as e:
                    logger.warning(f"Model compilation failed, using default mode: {e}")
            
            # Set to evaluation mode and optimize
            self.model.eval()
            if torch.cuda.is_available():
                # Enable CUDA optimization
                torch.backends.cudnn.benchmark = True
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True
                
                # Enable mixed precision inference (if supported)
                try:
                    self.model.half()
                    logger.info("Half precision inference enabled for faster performance")
                except Exception as e:
                    logger.warning(f"Half precision conversion failed, using full precision: {e}")
                
                # Optimize memory usage
                torch.cuda.empty_cache()
                logger.info("CUDA cache cleared")
            
            self.model_loaded = True
            logger.info(f"Qwen3-8B model loaded successfully")
            
        except Exception as e:
            logger.error(f"Qwen3-8B model loading failed: {e}")
            self.model_loaded = False
            raise
    
    def _format_messages(self, messages: List[Message]) -> str:
        """Format message list into Qwen format input, disable thinking mode"""
        # Use Qwen's chat template, disable thinking mode
        chat_messages = []
        for message in messages:
            if message.role == MessageRole.SYSTEM:
                chat_messages.append({"role": "system", "content": message.content})
            elif message.role == MessageRole.USER:
                chat_messages.append({"role": "user", "content": message.content})
            elif message.role == MessageRole.ASSISTANT:
                chat_messages.append({"role": "assistant", "content": message.content})
        
        # Use tokenizer's apply_chat_template method, disable thinking mode
        formatted_text = self.tokenizer.apply_chat_template(
            chat_messages,
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False  # Disable thinking mode
        )
        
        return formatted_text
    
    def _parse_response(self, response_text: str) -> str:
        """Parse model response, extract assistant content"""
        # Find assistant start marker
        start_marker = "assistant\n"
        end_marker = "\n\n"
        
        if start_marker in response_text:
            start_idx = response_text.find(start_marker) + len(start_marker)
            if end_marker in response_text[start_idx:]:
                end_idx = response_text.find(end_marker, start_idx)
                return response_text[start_idx:end_idx].strip()
            else:
                return response_text[start_idx:].strip()
        
        return response_text.strip()
    
    @handle_llm_errors
    async def chat(self, messages: List[Message], **kwargs) -> ChatResponse:
        """Chat conversation"""
        if not self.model_loaded:
            raise RuntimeError("Model not loaded")
        
        start_time = time.time()
        
        try:
            # Format input
            formatted_input = self._format_messages(messages)
            
            # Encode input
            inputs = self.tokenizer(
                formatted_input,
                return_tensors="pt",
                max_length=self.max_length,
                truncation=True
            )
            
            # Move to correct device
            if hasattr(self.model, 'device'):
                inputs = {k: v.to(self.model.device) for k, v in inputs.items()}
            
            # Generate parameters - optimize configuration for faster inference
            generation_config = {
                "max_new_tokens": min(self.config.max_tokens or 512, 1024),  # Limit maximum generation length
                "temperature": self.config.temperature,
                "top_p": self.config.top_p,
                "do_sample": True,
                "pad_token_id": self.tokenizer.eos_token_id,
                "eos_token_id": self.tokenizer.eos_token_id,
                "repetition_penalty": 1.05,  # Slight repetition penalty
                "no_repeat_ngram_size": 2,  # Reduce n-gram checking for faster performance
                "use_cache": True,  # Enable KV cache
                "num_beams": 1,  # Use greedy decoding instead of beam search
                "output_scores": False,  # Do not output scores for faster performance
                "return_dict_in_generate": False,  # Simplify return format
            }
            
            # Generate response - use more efficient inference mode
            with torch.inference_mode():
                outputs = self.model.generate(
                    **inputs,
                    **generation_config
                )
            
            # Decode response
            input_length = inputs['input_ids'].shape[1]
            logger.info(f"Input length: {input_length}, output length: {len(outputs[0])}")
            
            if len(outputs[0]) > input_length:
                response_text = self.tokenizer.decode(
                    outputs[0][input_length:],
                    skip_special_tokens=True
                )
                logger.info(f"Decoded response text: {response_text[:100]}...")
            else:
                response_text = ""
                logger.warning("Output length not greater than input length, returning empty response")
            
            # Parse response
            content = self._parse_response(response_text)
            
            # Calculate token usage
            input_tokens = inputs['input_ids'].shape[1]
            output_tokens = len(outputs[0]) - input_tokens if len(outputs[0]) > input_tokens else 0
            
            response_time = time.time() - start_time
            
            return ChatResponse(
                content=content,
                finish_reason="stop",
                model=self.config.model_name,
                usage={
                    "prompt_tokens": input_tokens,
                    "completion_tokens": output_tokens,
                    "total_tokens": input_tokens + output_tokens
                },
                response_time=response_time,
                metadata={
                    "model_path": self.model_path,
                    "device": str(self.model.device) if hasattr(self.model, 'device') else "unknown"
                }
            )
            
        except Exception as e:
            logger.error(f"Chat generation failed: {e}")
            raise
    
    @handle_llm_errors
    async def chat_stream(self, messages: List[Message], **kwargs) -> AsyncIterator[str]:
        """Streaming chat conversation"""
        if not self.model_loaded:
            raise RuntimeError("Model not loaded")
        
        try:
            # Format input
            formatted_input = self._format_messages(messages)
            
            # Encode input
            inputs = self.tokenizer(
                formatted_input,
                return_tensors="pt",
                max_length=self.max_length,
                truncation=True
            )
            
            # Move to correct device
            if hasattr(self.model, 'device'):
                inputs = {k: v.to(self.model.device) for k, v in inputs.items()}
            
            # Generate parameters - optimize configuration for faster inference
            generation_config = {
                "max_new_tokens": min(self.config.max_tokens or 512, 1024),  # Limit maximum generation length
                "temperature": self.config.temperature,
                "top_p": self.config.top_p,
                "do_sample": True,
                "pad_token_id": self.tokenizer.eos_token_id,
                "eos_token_id": self.tokenizer.eos_token_id,
                "repetition_penalty": 1.05,  # Slight repetition penalty
                "no_repeat_ngram_size": 2,  # Reduce n-gram checking for faster performance
                "use_cache": True,  # Enable KV cache
                "num_beams": 1,  # Use greedy decoding instead of beam search
                "output_scores": False,  # Do not output scores for faster performance
                "return_dict_in_generate": False,  # Simplify return format
            }
            
            # Streaming generation - use more efficient inference mode
            with torch.inference_mode():
                outputs = self.model.generate(
                    **inputs,
                    **generation_config
                )
                
                # Decode complete response
                input_length = inputs['input_ids'].shape[1]
                if len(outputs[0]) > input_length:
                    response_text = self.tokenizer.decode(
                        outputs[0][input_length:],
                        skip_special_tokens=True
                    )
                    # Simulate streaming output
                    words = response_text.split()
                    for i, word in enumerate(words):
                        if i == 0:
                            yield word
                        else:
                            yield " " + word
                        await asyncio.sleep(0.01)  # Simulate delay
                        
        except Exception as e:
            logger.error(f"Streaming chat generation failed: {e}")
            raise
    
    @handle_llm_errors
    async def complete(self, prompt: str, **kwargs) -> str:
        """Text completion"""
        messages = [Message(role=MessageRole.USER, content=prompt)]
        response = await self.chat(messages, **kwargs)
        return response.content
    
    @handle_llm_errors
    async def embed(self, text: str, **kwargs) -> EmbeddingResponse:
        """Text embedding - Qwen3-8B does not support embedding functionality"""
        raise NotImplementedError("Qwen3-8B does not support text embedding functionality")
    
    @handle_llm_errors
    async def embed_batch(self, texts: List[str], **kwargs) -> List[EmbeddingResponse]:
        """Batch text embedding - Qwen3-8B does not support embedding functionality"""
        raise NotImplementedError("Qwen3-8B does not support text embedding functionality")
    
    @handle_llm_errors
    async def summarize(self, text: str, **kwargs) -> str:
        """Text summarization"""
        prompt = f"Please generate a summary for the following text:\n\n{text}\n\nSummary:"
        return await self.complete(prompt, **kwargs)
    
    @handle_llm_errors
    async def validate_model(self, model_name: str) -> bool:
        """Validate if model is available"""
        return self.model_loaded and model_name in self.supported_models
    
    @handle_llm_errors
    async def get_model_info(self, model_name: str) -> Dict[str, Any]:
        """Get model information"""
        if not self.model_loaded:
            return {"error": "Model not loaded"}
        
        info = {
            "model_name": model_name,
            "model_path": self.model_path,
            "device": str(self.model.device) if hasattr(self.model, 'device') else "unknown",
            "max_length": self.max_length,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_tokens,
            "supported_models": self.supported_models
        }
        
        return info
