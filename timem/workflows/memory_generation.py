"""
TiMem Memory Generation Workflow

Memory generation workflow implemented based on LangGraph, with complete node design and state management.
The refactored version enhances error handling, state validation, and dependency injection mechanisms, improving stability and testability.
"""

from typing import Dict, List, Any, Optional
from datetime import datetime, timedelta
import asyncio
import logging
import traceback
import time
import json
import calendar
import sys

from langgraph.graph import StateGraph, END
import atexit

from timem.workflows.state import MemoryState, MemoryStateValidator
# Decoupled workflow only needs to use these components in run_backfill
# run() method imports and uses them directly internally, no need to import at class level
from timem.utils.time_manager import get_time_manager, TimeManager
from timem.utils.session_tracker import get_session_tracker, SessionTracker
from timem.utils.memory_accessor import get_memory_indexer
from timem.utils.logging import get_logger

logger = get_logger(__name__)

class MemoryGenerationWorkflow:
    """
    Memory Generation Workflow Class
    
    Responsible for coordinating the complete memory generation process, including:
    1. Decision Generation (TokenGenerator)
    2. History Collection (HistoryCollector)
    3. Memory Generation (MultiLayerMemoryGenerator)
    4. Memory Storage (StorageRouter)
    
    Note: Should call cleanup() method to release resources after use to avoid unclean program exit.
    """
    
    def __init__(self, 
                 time_manager: Optional[TimeManager] = None, 
                 session_tracker: Optional[SessionTracker] = None, 
                 memory_indexer: Optional[Any] = None, 
                 state_validator: Optional[MemoryStateValidator] = None,
                 debug_mode: bool = False):
        """
        Initialize workflow (synchronous part)
        
        Args:
            time_manager: Time manager, auto-fetched if None
            session_tracker: Session tracker, auto-fetched if None
            memory_indexer: Memory indexer, auto-fetched if None
            state_validator: State validator, creates new instance if None
            debug_mode: Whether to enable debug mode, outputs more information in debug mode
        """
        self.time_manager = time_manager
        self.session_tracker = session_tracker
        self.memory_indexer = memory_indexer
        self.state_validator = state_validator or MemoryStateValidator()
        self.debug_mode = debug_mode
        
        # Workflow components and state
        self.app = None  # LangGraph application, compiled in async initialization
        self.graph = None  # LangGraph state graph
        self.nodes = {}  # Workflow node instances

    async def _async_init(self):
        """
        Async initialization part (decoupled version)
        
        Only initialize necessary dependency components, no longer build LangGraph graph
        """
        # Initialize dependency components
        if self.time_manager is None:
            self.time_manager = get_time_manager()
            
        if self.session_tracker is None:
            self.session_tracker = await get_session_tracker()
            
        if self.memory_indexer is None:
            self.memory_indexer = await get_memory_indexer()
        
        logger.info(f"Initialize workflow (decoupled version), debug mode: {self.debug_mode}")
        
        # No longer build and compile graph, run() method directly uses decoupled components
        # Keep these attributes as None for compatibility
        self.graph = None
        self.app = None
        
        logger.info("Workflow initialization completed (directly call decoupled components, no graph structure needed)")
        
    @classmethod
    async def create(cls, **kwargs):
        """
        Async factory method for creating and initializing workflow instances
        
        Args:
            **kwargs: Passed to __init__ parameters
        
        Returns:
            Initialized workflow instance
        """
        instance = cls(**kwargs)
        await instance._async_init()
        return instance

    async def _build_graph(self) -> StateGraph:
        """
        Build workflow graph (deprecated, kept for backward compatibility)
        
        ⚠️ Note: run() method has been refactored to directly call decoupled components, no longer uses LangGraph graph structure.
        This method is kept only for backward compatibility and will not actually be called.
        
        Returns:
            Empty StateGraph object
        """
        if self.debug_mode:
            print("[DEBUG] Building workflow graph (deprecated)...")
        
        logger.info("Workflow graph building method is deprecated, run() method now directly uses decoupled components")
        
        # Return an empty graph structure for compatibility
        from typing import Dict as TypingDict, Any
        workflow = StateGraph(TypingDict[str, Any])
        
        # No longer create nodes because run() method no longer uses graph structure
        logger.info("Workflow graph building completed (empty graph for compatibility)")
        
        return workflow
    
    async def run(self, input_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run workflow - Real-time L1 memory generation (decoupled version)
        
        Responsibility: Focus on real-time generation of L1 fragment memories within sessions
        
        Process:
        1. Validate input
        2. Register session
        3. Collect historical L1 (for context)
        4. Generate L1 memory
        5. Store L1 memory
        6. Return result
        
        Args:
            input_data: Input data, must contain user_id, expert_id, session_id, content fields
            
        Returns:
            Generation result, containing generated L1 memory
            
        Refactoring notes:
        - ✅ Remove MultiLayerMemoryGenerator (old component)
        - ✅ Remove TokenGenerator (complete decision logic)
        - ✅ Remove HistoryCollector (old history collection)
        - ✅ Use decoupled components: MemoryCollector → LayerMemoryGenerator → StorageRouter
        - ✅ Simplify process, focus on L1 real-time generation
        """
        start_time = time.time()
        current_step = "init"
        
        try:
            if self.debug_mode:
                print(f"\n[START] Start real-time L1 memory generation (decoupled version)...")
                
            logger.info(f"Start real-time L1 generation: {input_data.get('user_id')}-{input_data.get('expert_id')}-{input_data.get('session_id')}")
            
            # 1. Validate input data
            current_step = "validate_input"
            required_fields = ["user_id", "expert_id", "session_id", "content"]
            missing_fields = [field for field in required_fields if field not in input_data]
            
            if missing_fields:
                error_msg = f"Missing required input fields: {', '.join(missing_fields)}"
                logger.error(error_msg)
                return self._create_error_response(error_msg)
            
            # 2. Extract basic parameters
            current_step = "extract_params"
            user_id = input_data["user_id"]
            expert_id = input_data["expert_id"]
            session_id = input_data["session_id"]
            content = input_data["content"]
            timestamp = input_data.get("timestamp", datetime.now())
            
            if self.debug_mode:
                print(f"[PARAMS] user={user_id}, expert={expert_id}, session={session_id}")
            
            # 3. Register session (continue even if failed)
            current_step = "register_session"
            try:
                if self.debug_mode:
                    print(f"[REGISTER] Register session: {session_id}")
                    
                await self.session_tracker.register_session(
                    session_id,
                    user_id,
                    expert_id,
                    timestamp
                )
                logger.info(f"Session registration successful: {session_id}")
            except Exception as e:
                # Session registration failure should not block workflow execution, log and continue
                logger.warning(f"Session registration failed: {str(e)}, but workflow will continue")
                if self.debug_mode:
                    print(f"[WARNING] Session registration failed: {str(e)}")
            
            # 4. Collect historical L1 (for context)
            current_step = "collect_history"
            try:
                if self.debug_mode:
                    print(f"[COLLECT] Collect historical L1...")
                
                from timem.workflows.nodes.memory_collector import MemoryCollector
                from storage.memory_storage_manager import get_memory_storage_manager_async
                
                storage_manager = await get_memory_storage_manager_async()
                collector = MemoryCollector(storage_manager)
                
                collected = await collector.collect_for_layer(
                    layer="L1",
                    user_id=user_id,
                    expert_id=expert_id,
                    session_id=session_id,
                    time_window=None,
                    historical_limit=3  # Collect first 3 L1 as context
                )
                
                logger.info(f"Historical L1 collection completed: {len(collected.historical_memories)} items")
                if self.debug_mode:
                    print(f"[COLLECT] Collected {len(collected.historical_memories)} historical L1")
                    
            except Exception as e:
                # Collection failure does not affect generation, use empty history
                logger.warning(f"Historical L1 collection failed: {e}, will use empty history")
                if self.debug_mode:
                    print(f"[WARNING] History collection failed: {e}")
                
                from timem.workflows.nodes.memory_collector import CollectedMemories
                collected = CollectedMemories(
                    child_memories=[],
                    historical_memories=[]
                )
            
            # 5. Generate L1 memory
            current_step = "generate_l1"
            try:
                if self.debug_mode:
                    print(f"[GENERATE] Generate L1 memory...")
                
                # Directly use L1Processor (simplified version)
                # L1Processor is now imported from unified_processors (in code below)
                
                # Build state dictionary (format needed by L1Processor.process())
                state = {
                    "user_id": user_id,
                    "expert_id": expert_id,
                    "session_id": session_id,
                    "content": content,
                    "timestamp": timestamp,
                    "dialogue_id": f"{session_id}_{int(timestamp.timestamp())}",
                    "L1_historical_memory_ids": collected.historical_memories  # Pass historical memories for processor use
                }
                
                # Create L1Processor (use unified unified_processors)
                from timem.workflows.nodes.unified_processors import L1Processor
                processor = L1Processor()
                
                # Call process method (only pass state)
                result_state = await processor.process(state)
                
                # Extract generated memory from result state
                if "error" in result_state:
                    raise ValueError(result_state["error"])
                
                # Get generated memory (L1Processor adds memory to state)
                memory = result_state.get("generated_memory") or result_state.get("memory")
                
                logger.info(f"L1 memory generation successful: {getattr(memory, 'id', 'Unknown')}")
                if self.debug_mode:
                    memory_id = getattr(memory, 'id', 'Unknown')
                    print(f"[GENERATE] ✅ L1 generation successful: {memory_id}")
                    
            except Exception as e:
                error_msg = f"L1 memory generation failed: {str(e)}"
                logger.error(error_msg, exc_info=True)
                if self.debug_mode:
                    print(f"[GENERATE] ❌ L1 generation failed: {e}")
                return self._create_error_response(error_msg, traceback.format_exc())
            
            # 6. Store L1 memory
            current_step = "store_memory"
            try:
                if self.debug_mode:
                    print(f"[STORE] Store L1 memory...")
                
                # Ensure memory exists
                if not memory:
                    raise ValueError("Failed to generate valid L1 memory object")
                
                from timem.workflows.nodes.storage_router import StorageRouter
                
                storage_router = StorageRouter()
                
                # Build storage state (compatible with StorageRouter interface)
                store_state = {
                    "generated_memories": [memory],
                    "user_id": user_id,
                    "expert_id": expert_id,
                    "session_id": session_id,
                    "timestamp": timestamp
                }
                
                await storage_router.run(store_state)
                
                logger.info(f"L1 memory storage successful")
                if self.debug_mode:
                    print(f"[STORE] ✅ L1 storage successful")
                
            except Exception as e:
                error_msg = f"L1 memory storage failed: {str(e)}"
                logger.error(error_msg, exc_info=True)
                if self.debug_mode:
                    print(f"[STORE] ❌ L1 storage failed: {e}")
                return self._create_error_response(error_msg, traceback.format_exc())
            
            # 7. Convert Memory object to dictionary (maintain backward compatibility)
            current_step = "convert_memory"
            try:
                # memory is already in dictionary format (returned from processor)
                if isinstance(memory, dict):
                    memory_dict = memory
                else:
                    # If it's an object, convert to dictionary
                    from timem.utils.memory_object_utils import batch_convert_memories
                    converted_memories = batch_convert_memories([memory])
                    memory_dict = converted_memories[0] if converted_memories else {}

                logger.info(f"Memory object conversion completed")

            except Exception as e:
                logger.warning(f"Memory object conversion failed: {e}, using original object")
                memory_dict = memory if isinstance(memory, dict) else {
                    "id": getattr(memory, 'id', 'Unknown'),
                    "level": getattr(memory, 'level', 'L1'),
                    "content": getattr(memory, 'content', ''),
                    "user_id": user_id,
                    "expert_id": expert_id
                }

            # 8. Build return result
            current_step = "build_response"
            end_time = time.time()
            execution_time = end_time - start_time

            final_state = {
                "success": True,
                "generated_memories": [memory_dict],
                "user_id": user_id,
                "expert_id": expert_id,
                "session_id": session_id,
                "timestamp": timestamp,
                "execution_stats": {
                    "start_time": start_time,
                    "end_time": end_time,
                    "execution_time": execution_time,
                    "timestamp": datetime.now().isoformat()
                }
            }

            # 9. Output execution summary
            if self.debug_mode:
                print(f"\n[SUCCESS] L1 memory generation completed")
                print(f"⏱️ Execution time: {execution_time:.2f} seconds")
                print(f"📝 Generated memory: {memory_dict.get('id', 'Unknown')}")
                print(f"💾 Level: {memory_dict.get('level', 'Unknown')}")

            logger.info(f"L1 real-time generation completed, elapsed time: {execution_time:.2f}s")
            return final_state

        except ValueError as ve:
            error_msg = f"Workflow interrupted due to validation failure: {ve} (current step: {current_step})"
            logger.error(error_msg)
            if self.debug_mode:
                print(f"[ERROR] {error_msg}")
            return self._create_error_response(str(ve), traceback.format_exc())

        except Exception as e:
            error_msg = f"Unhandled exception during workflow execution: {e} (current step: {current_step})"
            logger.error(error_msg)
            logger.error(traceback.format_exc())
            if self.debug_mode:
                print(f"[ERROR] {error_msg}")
            return self._create_error_response(str(e), traceback.format_exc())

    def _create_error_response(self, error_msg: str, traceback_info: str = None, checkpoint: Dict = None) -> Dict[str, Any]:
        """
        Create standardized error response

        Args:
            error_msg: Error message
            traceback_info: Stack trace information
            checkpoint: State checkpoint

        Returns:
            Standardized error response dictionary
        """
        response = {
            "success": False,
            "error": error_msg,
            "timestamp": datetime.now().isoformat()
        }

        if traceback_info:
            response["traceback"] = traceback_info

        if checkpoint:
            response["checkpoint"] = checkpoint

        # Add simple memory and execution environment information for debugging
        try:
            import os
            import psutil
            process = psutil.Process(os.getpid())
            response["system_info"] = {
                "memory_usage_mb": process.memory_info().rss / 1024 / 1024,
                "cpu_percent": process.cpu_percent(interval=0.1),
                "python_version": sys.version.split()[0]
            }
        except ImportError:
            # psutil may not be installed, ignore system information collection
            pass
        except Exception as e:
            logger.warning(f"Failed to collect system information: {str(e)}")

        return response

    def _print_memory_summary(self, state: Dict[str, Any]) -> None:
        """
        Print memory generation summary, including memory level statistics and relationship information

        Args:
            state: Workflow state, containing generated memories
        """
        if "generated_memories" in state and state["generated_memories"]:
            memory_counts = {}
            for memory in state["generated_memories"]:
                # Handle Pydantic objects and dictionaries
                if hasattr(memory, 'level'):
                    level = memory.level
                elif isinstance(memory, dict):
                    level = memory.get("level", "Unknown")
                else:
                    level = "Unknown"
                    
                if level not in memory_counts:
                    memory_counts[level] = 0
                memory_counts[level] += 1
                
            print(f"📊 Generated memory summary:")
            for level, count in memory_counts.items():
                print(f"  - {level}: {count} memories")
                
            # Print an example of one memory
            print("\n📄 Memory example:")
            for level in sorted(memory_counts.keys()):
                for memory in state["generated_memories"]:
                    # Handle Pydantic objects and dictionaries
                    if hasattr(memory, 'level'):
                        mem_level = memory.level
                    elif isinstance(memory, dict):
                        mem_level = memory.get("level")
                    else:
                        mem_level = None
                        
                    if mem_level == level:
                        # Get content preview
                        if hasattr(memory, 'content'):
                            content_preview = memory.content
                        elif isinstance(memory, dict):
                            content_preview = memory.get('content', '')
                        else:
                            content_preview = ''
                            
                        # Get ID
                        if hasattr(memory, 'id'):
                            mem_id = memory.id
                        elif isinstance(memory, dict):
                            mem_id = memory.get('id', 'no-id')
                        else:
                            mem_id = 'no-id'
                            
                        if content_preview:
                            if len(content_preview) > 100:
                                content_preview = content_preview[:100] + "..."
                            print(f"  [{level}] {mem_id}: {content_preview}")
                        else:
                            print(f"  [{level}] {mem_id}: <empty content>")
                        break
            
            # Print relationship information
            has_relations = False
            for memory in state["generated_memories"]:
                # Check if there is relationship data - may be relations or child_memory_ids
                has_relationship_data = False
                relationship_info = ""
                
                # Get memory ID and level
                if hasattr(memory, 'id'):
                    mem_id = memory.id
                elif isinstance(memory, dict):
                    mem_id = memory.get('id', 'no-id')
                else:
                    mem_id = 'no-id'
                    
                if hasattr(memory, 'level'):
                    mem_level = memory.level
                elif isinstance(memory, dict):
                    mem_level = memory.get('level', 'Unknown')
                else:
                    mem_level = 'Unknown'
                
                # Check relations field
                if hasattr(memory, 'relations') and memory.relations:
                    has_relationship_data = True
                    relation_types = {}
                    
                    for relation in memory.relations:
                        if hasattr(relation, 'relation_type'):
                            rel_type = relation.relation_type
                        elif isinstance(relation, dict):
                            rel_type = relation.get("relation_type", "Unknown relation")
                        else:
                            rel_type = "Unknown relation"
                            
                        if rel_type not in relation_types:
                            relation_types[rel_type] = 0
                        relation_types[rel_type] += 1
                    
                    relationship_info = ", ".join([f"{count} {rtype}" for rtype, count in relation_types.items()])
                
                # Check child_memory_ids field
                elif hasattr(memory, 'child_memory_ids') and memory.child_memory_ids:
                    has_relationship_data = True
                    child_count = len(memory.child_memory_ids)
                    relationship_info = f"{child_count} child memories"
                elif isinstance(memory, dict) and "child_memory_ids" in memory and memory["child_memory_ids"]:
                    has_relationship_data = True
                    child_count = len(memory["child_memory_ids"])
                    relationship_info = f"{child_count} child memories"
                
                # Output relationship information
                if has_relationship_data:
                    if not has_relations:
                        print("\n🔗 Memory relationship information:")
                        has_relations = True
                    
                    print(f"  [{mem_level}] {mem_id}: {relationship_info}")
    
    async def run_backfill(
        self, 
        tasks: List[Any],
        progress_callback: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Backfill mode: Generate one by one from task list (enhanced version)
        
        Process:
        1. Validate task list (already sorted)
        2. Call run_single_memory one by one
        3. Collect statistics
        4. Detailed progress callback (including layer, expert, step, etc.)
        
        Args:
            tasks: List of BackfillTask (already sorted)
            progress_callback: Optional progress callback function
                Signature: async def callback(progress_info: dict)
                progress_info contains:
                - current_index: Current task index
                - total_count: Total task count
                - progress: Progress percentage
                - current_task: Current task object
                - current_layer: Current layer
                - current_expert: Current expert ID
                - current_step: Current step description
                - success_count: Success count
                - failed_count: Failed count
                - task_result: Current task execution result
        
        Returns:
            Execution result statistics
        
        Features:
        - Support batch, but process one by one
        - Failure isolation, single failure does not affect entire batch
        - Detailed progress feedback, support real-time UI updates
        """
        from datetime import datetime
        
        start_time = datetime.now()
        logger.info(f"Start backfill mode: {len(tasks)} tasks")
        
        results = {
            "total": len(tasks),
            "success": 0,
            "failed": 0,
            "errors": [],
            "generated_memories": []
        }
        
        # Group statistics by layer and expert (for progress display)
        layer_stats = {}
        expert_stats = {}
        
        for i, task in enumerate(tasks):
            try:
                current_layer = getattr(task, 'layer', 'Unknown')
                current_expert = getattr(task, 'expert_id', 'Unknown')
                current_step = f"Generate {current_layer} memory"
                
                # Update layer statistics
                if current_layer not in layer_stats:
                    layer_stats[current_layer] = {"completed": 0, "total": 0, "failed": 0}
                layer_stats[current_layer]["total"] += 1
                
                # Update expert statistics
                if current_expert not in expert_stats:
                    expert_stats[current_expert] = {"completed": 0, "total": 0, "failed": 0}
                expert_stats[current_expert]["total"] += 1
                
                logger.info(f"Execute task {i+1}/{len(tasks)}: {current_layer} @ {getattr(task, 'time_window', None) or getattr(task, 'session_id', 'Unknown')}")
                
                # Send start progress callback
                if progress_callback:
                    progress_info = {
                        "current_index": i,
                        "total_count": len(tasks),
                        "progress": (i / len(tasks)) * 100,
                        "current_task": task,
                        "current_layer": current_layer,
                        "current_expert": current_expert,
                        "current_step": current_step,
                        "success_count": results["success"],
                        "failed_count": results["failed"],
                        "task_result": None,
                        "layer_stats": layer_stats.copy(),
                        "expert_stats": expert_stats.copy(),
                        "started_at": start_time.isoformat()
                    }
                    await progress_callback(progress_info)
                
                # Call single memory generation
                result = await self.run_single_memory(task)
                
                if result.get("success"):
                    results["success"] += 1
                    layer_stats[current_layer]["completed"] += 1
                    expert_stats[current_expert]["completed"] += 1
                    if "memory" in result:
                        results["generated_memories"].append(result["memory"])
                else:
                    results["failed"] += 1
                    layer_stats[current_layer]["failed"] += 1  
                    expert_stats[current_expert]["failed"] += 1
                    results["errors"].append({
                        "task": str(task),
                        "error": result.get("error", "Unknown error"),
                        "layer": current_layer,
                        "expert": current_expert
                    })
                
                # Send completion progress callback
                if progress_callback:
                    progress_info = {
                        "current_index": i + 1,
                        "total_count": len(tasks),
                        "progress": ((i + 1) / len(tasks)) * 100,
                        "current_task": task,
                        "current_layer": current_layer,
                        "current_expert": current_expert,
                        "current_step": f"{current_step} - {'Success' if result.get('success') else 'Failed'}",
                        "success_count": results["success"],
                        "failed_count": results["failed"],
                        "task_result": result,
                        "layer_stats": layer_stats.copy(),
                        "expert_stats": expert_stats.copy(),
                        "started_at": start_time.isoformat()
                    }
                    await progress_callback(progress_info)
                    
            except Exception as e:
                logger.error(f"Task execution failed: {e}", exc_info=True)
                results["failed"] += 1
                
                # Update statistics
                current_layer = getattr(task, 'layer', 'Unknown')
                current_expert = getattr(task, 'expert_id', 'Unknown')
                if current_layer in layer_stats:
                    layer_stats[current_layer]["failed"] += 1
                if current_expert in expert_stats:
                    expert_stats[current_expert]["failed"] += 1
                
                results["errors"].append({
                    "task": str(task),
                    "error": str(e),
                    "layer": current_layer,
                    "expert": current_expert
                })
                
                # Send error progress callback
                if progress_callback:
                    progress_info = {
                        "current_index": i + 1,
                        "total_count": len(tasks),
                        "progress": ((i + 1) / len(tasks)) * 100,
                        "current_task": task,
                        "current_layer": current_layer,
                        "current_expert": current_expert,
                        "current_step": f"Execution failed: {str(e)[:50]}",
                        "success_count": results["success"],
                        "failed_count": results["failed"],
                        "task_result": {"success": False, "error": str(e)},
                        "layer_stats": layer_stats.copy(),
                        "expert_stats": expert_stats.copy(),
                        "started_at": start_time.isoformat(),
                        "error": str(e)
                    }
                    await progress_callback(progress_info)
        
        end_time = datetime.now()
        execution_time = (end_time - start_time).total_seconds()
        
        logger.info(f"Backfill completed: Success {results['success']}/{results['total']}, elapsed time {execution_time:.2f}s")
        
        # Add execution time and final statistics
        results.update({
            "execution_time": execution_time,
            "layer_stats": layer_stats,
            "expert_stats": expert_stats,
            "started_at": start_time.isoformat(),
            "completed_at": end_time.isoformat()
        })
        
        return results
    
    async def run_single_memory(
        self,
        task: Any
    ) -> Dict[str, Any]:
        """
        Core execution unit: Generate single memory (new method)
        
        Process:
        1. Call memory_collector to collect dependencies
        2. Call layer_generator to generate memory
        3. Call storage_router to store
        4. Return result (success/failure)
        
        Args:
            task: BackfillTask object
        
        Returns:
            Single memory generation result
        
        Features:
        - Atomic operation, generate and store immediately
        - Clear transaction, easy to rollback
        - Reusable, called by run_backfill
        - Enhanced retry mechanism for L2 tasks
        """
        logger.info(f"Start generating single memory: {task.layer} @ {task.time_window or task.session_id}")
        
        # Special handling for L2 tasks: Enhanced retry mechanism
        if task.layer == "L2":
            max_retries = 5  # L2 tasks allow 5 retries
            retry_count = 0
            last_error = None
            
            while retry_count <= max_retries:
                try:
                    result = await self._generate_single_memory_internal(task)
                    
                    if result.get("success"):
                        if retry_count > 0:
                            logger.info(f"L2 task retry successful: session={task.session_id} (Retry {retry_count})")
                        else:
                            logger.info(f"L2 task successful: session={task.session_id}")
                        return result
                    else:
                        last_error = result.get("error", "Unknown error")
                        
                        if retry_count < max_retries:
                            retry_count += 1
                            delay = min(2.0 * retry_count, 10.0)
                            logger.warning(f"L2 task failed, retry in {delay:.1f}s (Retry {retry_count}/{max_retries}): {last_error}")
                            await asyncio.sleep(delay)
                            continue
                        else:
                            logger.error(f"L2 task failed, retried {max_retries} times: session={task.session_id}, error={last_error}")
                            return {
                                "success": False,
                                "error": f"L2 generation failed (after {max_retries} retries): {last_error}",
                                "task": task
                            }
                            
                except Exception as e:
                    last_error = str(e)
                    
                    if retry_count < max_retries:
                        retry_count += 1
                        delay = min(2.0 * retry_count, 10.0)
                        logger.warning(f"L2 task exception, retry in {delay:.1f}s (Retry {retry_count}/{max_retries}): {e}")
                        await asyncio.sleep(delay)
                        continue
                    else:
                        logger.error(f"L2 task exception, retried {max_retries} times: session={task.session_id}, error={e}", exc_info=True)
                        return {
                            "success": False,
                            "error": f"L2 generation exception (after {max_retries} retries): {e}",
                            "task": task
                        }
        
        # Other layers: Normal processing (no additional retries)
        try:
            return await self._generate_single_memory_internal(task)
        except Exception as e:
            logger.error(f"Single memory generation failed: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "task": task
            }
    
    async def _generate_single_memory_internal(
        self,
        task: Any
    ) -> Dict[str, Any]:
        """
        Internal method: Actual memory generation logic
        """
        # 1. Collect dependencies
        from timem.workflows.nodes.memory_collector import MemoryCollector
        from storage.memory_storage_manager import get_memory_storage_manager_async
        
        storage_manager = await get_memory_storage_manager_async()
        collector = MemoryCollector(storage_manager)
        
        collected = await collector.collect_for_layer(
            layer=task.layer,
            user_id=task.user_id,
            expert_id=task.expert_id,
            session_id=task.session_id,
            time_window=task.time_window,
            historical_limit=3
        )
        
        logger.info(f"Collection completed: child_memories={len(collected.child_memories)}, historical={len(collected.historical_memories)}")
        
        # 2. Generate memory
        from timem.workflows.nodes.layer_memory_generator import LayerMemoryGenerator
        
        generator = LayerMemoryGenerator()
        
        memory = await generator.generate(
            layer=task.layer,
            user_id=task.user_id,
            expert_id=task.expert_id,
            session_id=task.session_id,
            time_window=task.time_window,
            collected_memories=collected,
            timestamp=task.timestamp or datetime.now()
        )
        
        logger.info(f"Memory generation successful: {getattr(memory, 'id', 'Unknown')}")
        
        # 3. Handle idempotency and store memory
        from timem.workflows.nodes.storage_router import StorageRouter
        
        storage_router = StorageRouter()
        
        # Check if idempotency handling is needed (force update mode)
        if getattr(task, 'force_update', False):
            logger.info(f"Force update mode: checking for existing memory")
            
            # Find existing memory
            existing_memory = await self._find_existing_memory(task, storage_manager)
            
            if existing_memory:
                existing_memory_id = getattr(existing_memory, 'id', existing_memory.get('id') if isinstance(existing_memory, dict) else None)
                logger.info(f"Found existing memory, executing update: {existing_memory_id}")
                
                # Update existing memory
                updates = {
                    "content": getattr(memory, 'content', memory.get('content') if isinstance(memory, dict) else ''),
                    "title": getattr(memory, 'title', memory.get('title') if isinstance(memory, dict) else ''),
                    "importance_score": getattr(memory, 'importance_score', memory.get('importance_score') if isinstance(memory, dict) else 0.0),
                    "updated_at": task.timestamp or datetime.now(),
                    "metadata": getattr(memory, 'metadata', memory.get('metadata') if isinstance(memory, dict) else {})
                }
                
                # Execute update
                update_result = await storage_manager.update_memory(existing_memory_id, updates)
                
                # Return update result
                updated = {
                    "success": any(update_result.values()) if update_result else False,
                    "memory_id": existing_memory_id,
                    "action": "updated",
                    "task": task,
                    "update_result": update_result
                }
                if updated["success"]:
                    await self._schedule_memory_graph(task, memory)
                return updated
            else:
                logger.info(f"Force update mode but no existing memory found, executing create")
        
        # Build state for storage
        state = {
            "generated_memories": [memory],
            "user_id": task.user_id,
            "expert_id": task.expert_id,
            "session_id": task.session_id or "",
            "timestamp": task.timestamp or datetime.now()
        }
        
        await storage_router.run(state)
        
        logger.info(f"Memory storage successful")
        await self._schedule_memory_graph(task, memory)

        return {
            "success": True,
            "memory": memory,
            "action": "created",
            "task": task
        }

    async def _schedule_memory_graph(self, task: Any, memory: Any) -> None:
        """L3 success enqueues child-L2 graph ingest. Failures leave the L3 in place."""
        if getattr(task, "layer", None) != "L3":
            return
        from timem.memory_graph.schedule import safe_schedule_after_l3

        await safe_schedule_after_l3(task, memory)
    
    async def _find_existing_memory(self, task: Any, storage_manager: Any) -> Optional[Any]:
        """
        Find existing memory
        
        Args:
            task: Backfill task
            storage_manager: Storage manager
            
        Returns:
            Existing memory object, or None if not found
        """
        try:
            # Build query conditions
            query = {
                "user_id": task.user_id,
                "expert_id": task.expert_id,
                "layer": task.layer
            }
            
            # L2 uses session_id query
            if task.layer == "L2" and task.session_id:
                query["session_id"] = task.session_id
            # L3-L5 use time range query
            elif task.time_window:
                query["time_window_start"] = task.time_window.get("start_time")
                query["time_window_end"] = task.time_window.get("end_time")
            else:
                logger.warning(f"Unable to build query conditions: task={task}")
                return None
            
            # Query memory
            memories = await storage_manager.search_memories(query, {
                "sort_by": "created_at",
                "sort_order": "desc", 
                "limit": 1
            })
            
            if memories:
                logger.info(f"Found existing memory: {len(memories)} items")
                return memories[0]
            else:
                logger.info(f"No existing memory found")
                return None
                
        except Exception as e:
            logger.error(f"Find existing memory failed: {e}", exc_info=True)
            return None

    async def cleanup(self):
        """
        Clean up workflow resources, close all connections
        
        This method should be called after the workflow is finished to ensure all resources are properly released,
        especially database connections, cache connections, etc., to avoid memory leaks and program exit issues.
        """
        logger.info("Start cleaning up workflow resources...")
        
        # Mark as cleaned to avoid duplicate cleanup
        if hasattr(self, '_is_cleaned') and self._is_cleaned:
            logger.info("Workflow resources already cleaned, skip duplicate cleanup")
            return
        
        # Clean up node resources
        for node_name, node in self.nodes.items():
            if hasattr(node, 'cleanup') and callable(getattr(node, 'cleanup')):
                try:
                    if asyncio.iscoroutinefunction(node.cleanup):
                        await node.cleanup()
                    else:
                        node.cleanup()
                    logger.info(f"Node {node_name} resources cleaned up")
                except Exception as e:
                    logger.error(f"Error cleaning up node {node_name} resources: {e}")
        
            # Close storage connections
            try:
                if "storage_router" in self.nodes and hasattr(self.nodes["storage_router"], "storage_manager"):
                    storage_manager = self.nodes["storage_router"].storage_manager
                    if storage_manager:
                        # First monitor connection pool status
                        try:
                            if hasattr(storage_manager, "monitor_connection_pools"):
                                monitor_result = await storage_manager.monitor_connection_pools()
                                if monitor_result.get("issues"):
                                    logger.warning(f"Detected connection pool issues: {monitor_result['issues']}")
                        except Exception as e:
                            logger.warning(f"Failed to monitor connection pool status: {e}")
                        
                        # Force close all connections
                        if hasattr(storage_manager, "close_all_connections"):
                            await storage_manager.close_all_connections()
                            logger.info("Storage manager connections closed")
                
                # Fix: Additional cleanup of global connection pool manager
                try:
                    from storage.connection_pool_manager import shutdown_connection_pool
                    await shutdown_connection_pool()
                    logger.info("Global connection pool manager closed")
                except Exception as e:
                    logger.warning(f"Failed to close global connection pool manager: {e}")
                    
            except Exception as e:
                logger.error(f"Error closing storage connections: {e}")
            
        # Close session tracker connections
        try:
            if self.session_tracker and hasattr(self.session_tracker, "close"):
                if asyncio.iscoroutinefunction(self.session_tracker.close):
                    await self.session_tracker.close()
                else:
                    self.session_tracker.close()
                logger.info("Session tracker connections closed")
        except Exception as e:
            logger.error(f"Error closing session tracker connections: {e}")
            
        # Close memory indexer connections
        try:
            if self.memory_indexer and hasattr(self.memory_indexer, "close"):
                if asyncio.iscoroutinefunction(self.memory_indexer.close):
                    await self.memory_indexer.close()
                else:
                    self.memory_indexer.close()
                logger.info("Memory indexer connections closed")
        except Exception as e:
            logger.error(f"Error closing memory indexer connections: {e}")
        
        # Clean up other resources
        self.app = None  # Help GC collection
        self.graph = None
        
_workflow_instance = None
_workflow_lock = asyncio.Lock()
_cleanup_registered = False
# Avoid duplicate cleanup during process exit and log writing to closed stream errors
_cleanup_done = False

def _cleanup_workflow_sync():
    global _workflow_instance
    global _cleanup_done
    if _workflow_instance is None:
        return
    # If cleanup is already done, don't repeat
    if _cleanup_done or getattr(_workflow_instance, "_is_cleaned", False):
        return
    try:
        # Ensure resources are released during process exit (create new event loop for async cleanup)
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            # During exit phase, avoid logging as much as possible, mark state first, cleanup handles internal logging
            loop.run_until_complete(_workflow_instance.cleanup())
        finally:
            loop.close()
        _cleanup_done = True
    except Exception as e:
        # During exit phase, avoid writing complex logs, simple output to stderr as fallback
        try:
            import sys
            sys.stderr.write(f"[TiMem] Failed to clean up workflow resources during exit: {e}\n")
        except Exception:
            pass

async def get_workflow(debug_mode: bool = False) -> MemoryGenerationWorkflow:
    """
    Get workflow singleton instance
    
    Args:
        debug_mode: Whether to enable debug mode
        
    Returns:
        Workflow instance
        
    Note:
        Should call cleanup() method on workflow instance after use to release resources
    """
    global _workflow_instance
    if _workflow_instance is None:
        async with _workflow_lock:
            if _workflow_instance is None:
                logger.info(f"Creating global workflow instance, debug mode: {debug_mode}")
                _workflow_instance = await MemoryGenerationWorkflow.create(debug_mode=debug_mode)
                # Register process exit cleanup hook (only register once)
                global _cleanup_registered
                if not _cleanup_registered:
                    try:
                        atexit.register(_cleanup_workflow_sync)
                        _cleanup_registered = True
                    except Exception as e:
                        logger.warning(f"Failed to register exit cleanup hook: {e}")
    # If instance exists but is cleaned (e.g., app is None), reinitialize
    if getattr(_workflow_instance, "app", None) is None or getattr(_workflow_instance, "graph", None) is None:
        async with _workflow_lock:
            if getattr(_workflow_instance, "app", None) is None or getattr(_workflow_instance, "graph", None) is None:
                logger.info("Detected global workflow is cleaned or not initialized, reinitializing...")
                await _workflow_instance._async_init()
                # After reinitialization, reset storage manager singleton to avoid referencing old connections
                try:
                    from storage.memory_storage_manager import _storage_manager_instance_async
                    if _storage_manager_instance_async is not None:
                        await _storage_manager_instance_async.close_all_connections()
                except Exception:
                    pass
    return _workflow_instance

async def run_memory_generation(input_data: Dict[str, Any], debug_mode: bool = False) -> Dict[str, Any]:
    """
    Main function to run memory generation workflow
    
    Args:
        input_data: Input data, must contain user_id, expert_id, session_id, content fields
        debug_mode: Whether to enable debug mode
        
    Returns:
        Workflow execution result
    """
    workflow = await get_workflow(debug_mode)
    return await workflow.run(input_data)


# ============================================================================
# Enhanced workflow (integrated user group state management)
# ============================================================================

class MemoryGenerationWorkflowEnhanced:
    """
    Enhanced memory generation workflow
    
    Adds to the base workflow:
    1. User group state management
    2. Atomic turn number operations
    3. State updates
    """
    
    def __init__(
        self,
        base_workflow: Optional[MemoryGenerationWorkflow] = None,
        db_session: Optional[Any] = None,
        debug_mode: bool = False
    ):
        """
        Initialize enhanced workflow
        
        Args:
            base_workflow: Base workflow instance (optional)
            db_session: Database session (for state management)
            debug_mode: Debug mode
        """
        self.base_workflow = base_workflow
        self.db_session = db_session
        self.debug_mode = debug_mode
        self.logger = get_logger(__name__)
        
        # State managers (lazy initialization)
        self.state_manager: Optional[Any] = None
        self.session_tracker_postgres: Optional[Any] = None
    
    async def _ensure_managers(self):
        """Ensure managers are initialized"""
        if self.db_session:
            if self.state_manager is None:
                from timem.core.user_group_state_manager import get_user_group_state_manager
                self.state_manager = await get_user_group_state_manager(self.db_session)
            
            if self.session_tracker_postgres is None:
                from timem.utils.session_tracker_postgres import get_session_tracker_postgres
                self.session_tracker_postgres = await get_session_tracker_postgres(self.db_session)
    
    async def run_with_state_management(
        self,
        input_data: Dict[str, Any],
        db_session: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        Run workflow (with state management)
        
        Complete process:
        1. Get user group state
        2. Atomically get turn number
        3. Execute base workflow
        4. Update user group state
        
        Args:
            input_data: Input data
            db_session: Database session (optional, overrides instance session if provided)
            
        Returns:
            Workflow execution result (with state information)
        """
        start_time = time.time()
        
        # Use provided session or instance session
        session = db_session or self.db_session
        
        try:
            # Extract key information
            user_id = input_data.get("user_id")
            expert_id = input_data.get("expert_id")
            session_id = input_data.get("session_id")
            
            if not all([user_id, expert_id, session_id]):
                raise ValueError("Missing required fields: user_id, expert_id, session_id")
            
            self.logger.info(
                f"🚀 Start enhanced memory generation: user={user_id}, "
                f"expert={expert_id}, session={session_id}"
            )
            
            # === Step 1: Initialize managers ===
            if session:
                await self._ensure_managers()
                
                # === Step 2: Get user group state ===
                user_group_state = await self.state_manager.get_or_create_state(
                    user_id, expert_id
                )
                
                self.logger.info(
                    f"📊 User group state: "
                    f"total_sessions={user_group_state.total_sessions}, "
                    f"total_memories_l1={user_group_state.total_memories_l1}"
                )
                
                # === Step 3: Register session (if needed) ===
                await self.session_tracker_postgres.register_session(
                    session_id,
                    user_id,
                    expert_id,
                    start_time=input_data.get("timestamp")
                )
                
                # === Step 4: Atomically get turn number ===
                try:
                    turn_number = await self.session_tracker_postgres.get_next_turn_number_atomic(
                        session_id, user_id, expert_id
                    )
                    
                    self.logger.info(f"🔢 Atomically get turn number: {turn_number}")
                    
                    # Add turn number to input_data
                    input_data["turn_number"] = turn_number
                    
                except ValueError as e:
                    # Session doesn't exist, already registered, retry
                    self.logger.warning(f"⚠️ Session may have just been registered, retrying to get turn number")
                    await asyncio.sleep(0.1)  # Brief delay
                    
                    turn_number = await self.session_tracker_postgres.get_next_turn_number_atomic(
                        session_id, user_id, expert_id
                    )
                    input_data["turn_number"] = turn_number
            
            # === Step 5: Execute base workflow ===
            if self.base_workflow:
                result = await self.base_workflow.run(input_data)
            else:
                # If no base workflow, use default
                workflow = await get_workflow(debug_mode=self.debug_mode)
                result = await workflow.run(input_data)
            
            # === Step 6: Update user group state ===
            if session and self.state_manager and result.get("success"):
                generated_memories = result.get("generated_memories", [])
                
                if generated_memories:
                    await self.state_manager.update_after_memory_generation(
                        user_id=user_id,
                        expert_id=expert_id,
                        session_id=session_id,
                        memories=generated_memories
                    )
                    
                    self.logger.info(
                        f"✅ User group state updated: generated {len(generated_memories)} memories"
                    )
            
            # === Step 7: Add enhanced metadata ===
            execution_time = time.time() - start_time
            
            result["enhanced_metadata"] = {
                "user_group_state_updated": session is not None,
                "turn_number": input_data.get("turn_number"),
                "execution_time": execution_time,
                "timestamp": datetime.now().isoformat()
            }
            
            self.logger.info(
                f"✅ Enhanced workflow completed: elapsed time {execution_time:.2f}s"
            )
            
            return result
            
        except Exception as e:
            self.logger.error(f"❌ Enhanced workflow failed: {e}")
            self.logger.error(traceback.format_exc())
            
            return {
                "success": False,
                "error": str(e),
                "traceback": traceback.format_exc(),
                "timestamp": datetime.now().isoformat()
            }


# Enhanced workflow factory function
async def create_enhanced_workflow(
    db_session: Optional[Any] = None,
    debug_mode: bool = False
) -> MemoryGenerationWorkflowEnhanced:
    """
    Create enhanced workflow instance
    
    Args:
        db_session: Database session
        debug_mode: Debug mode
        
    Returns:
        Enhanced workflow instance
    """
    # Get base workflow
    base_workflow = await get_workflow(debug_mode=debug_mode)
    
    # Create enhanced version
    enhanced_workflow = MemoryGenerationWorkflowEnhanced(
        base_workflow=base_workflow,
        db_session=db_session,
        debug_mode=debug_mode
    )
    
    return enhanced_workflow


# Enhanced workflow global singleton (optional)
_enhanced_workflow_instance: Optional[MemoryGenerationWorkflowEnhanced] = None
_enhanced_workflow_lock_enhanced = asyncio.Lock()


async def get_enhanced_workflow(
    db_session: Optional[Any] = None,
    debug_mode: bool = False
) -> MemoryGenerationWorkflowEnhanced:
    """
    Get enhanced workflow singleton
    
    Args:
        db_session: Database session
        debug_mode: Debug mode
        
    Returns:
        Enhanced workflow instance
    """
    global _enhanced_workflow_instance
    
    # Note: Since db_session may be different each time, we don't use strict singleton here
    # Instead, create new instance each time but reuse base_workflow
    
    if _enhanced_workflow_instance is None:
        async with _enhanced_workflow_lock_enhanced:
            if _enhanced_workflow_instance is None:
                _enhanced_workflow_instance = await create_enhanced_workflow(
                    db_session=db_session,
                    debug_mode=debug_mode
                )
    
    # If new db_session provided, update it
    if db_session:
        _enhanced_workflow_instance.db_session = db_session
        # Reset managers to use new session
        _enhanced_workflow_instance.state_manager = None
        _enhanced_workflow_instance.session_tracker_postgres = None
    
    return _enhanced_workflow_instance


async def run_memory_generation_enhanced(
    input_data: Dict[str, Any],
    db_session: Optional[Any] = None,
    debug_mode: bool = False
) -> Dict[str, Any]:
    """
    Convenient function to run enhanced memory generation workflow
    
    Args:
        input_data: Input data
        db_session: Database session
        debug_mode: Debug mode
        
    Returns:
        Workflow execution result
    """
    workflow = await get_enhanced_workflow(db_session=db_session, debug_mode=debug_mode)
    return await workflow.run_with_state_management(input_data, db_session=db_session)