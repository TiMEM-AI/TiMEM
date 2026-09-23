"""
Complex Retriever - Bottom-Up Reconstruction Version

Implements complex retrieval based on BottomUpRetrieverBase, solving context pollution in deep reasoning:
1. L1 hybrid retrieval (top5) - maintains factual foundation anchoring
2. Session scoring analysis to determine the most relevant time period
3. Deep association supplement: L5 deep profile of the best session's month
4. Intelligent intermediate layer supplement: decide whether to supplement L2, L3 based on question characteristics
5. Importance-weighted sorting: L1-dominated + L5 deep analysis

Design goals:
- Significantly improve accuracy from 5-10% decline to 85-90% (significant improvement)
- Maintain L1 factual foundation, avoid completely deviating from concrete information
- L5 provides deep profile analysis, enhancing reasoning capability
- Eliminate time-irrelevant memory pollution, provide high-quality context
"""

import time
from typing import Dict, List, Any, Optional

from timem.workflows.retrieval_nodes.bottom_up_retriever_base import BottomUpRetrieverBase
from timem.utils.logging import get_logger

logger = get_logger(__name__)


class ComplexRetriever(BottomUpRetrieverBase):
    """
    Complex Retriever - Bottom-Up Reconstruction Version
    
    Post-reconstruction strategy:
    1. Maintain L1 hybrid retrieval as foundation (avoid completely deviating from facts)
    2. Trace to monthly L5 deep profile based on best session
    3. Intelligently supplement intermediate layers (L2 sessions, L3 concepts)
    4. Importance-weighted sorting: L1 foundation + L5 deepening + optional intermediate layers
    """
    
    def __init__(self, strategy_config: Optional[Dict[str, Any]] = None, **kwargs):
        """Initialize ComplexRetriever"""
        super().__init__(strategy_config=strategy_config, **kwargs)
        
        # Read layer_limits and final_limits from strategy config
        self.strategy_config = strategy_config or {}
        layer_limits = self.strategy_config.get('layer_limits', {})
        final_limits = self.strategy_config.get('final_limits', {})
        
        # 🆕 Read enabled layers list from config
        self.enabled_layers = self.strategy_config.get('layers', ['L1', 'L3', 'L4', 'L5'])
        self.logger.info(f"Enabled layers: {self.enabled_layers}")
        
        # 🆕 Read whether to disable bottom-up mechanism from config
        self.disable_bottom_up = self.strategy_config.get('disable_bottom_up', False)
        if self.disable_bottom_up:
            self.logger.warning(f"⚠️ Bottom-up mechanism disabled, will only retrieve L1 memories")
        
        # Configure coarse and fine ranking limits for each layer
        self.l1_coarse_top_k = layer_limits.get('L1', 40)  # Coarse ranking count
        self.l1_final_top_k = final_limits.get('L1', 20)   # Fine ranking count
        self.l3_final_top_k = final_limits.get('L3', 2)    # L3 count
        self.l4_final_top_k = final_limits.get('L4', 1)    # L4 count
        self.l5_final_top_k = final_limits.get('L5', 1)    # L5 count
        
        # Set strategy name for parent class
        self.strategy_name = 'complex'
        
        self.logger.info(f"Initialize ComplexRetriever - Bottom-Up Reconstruction Version")
        self.logger.info(f"   Enabled layers: {self.enabled_layers}")
        self.logger.info(f"   L1 coarse: {self.l1_coarse_top_k} items, L1 fine: {self.l1_final_top_k} items")
        self.logger.info(f"   L3: {self.l3_final_top_k} items, L4: {self.l4_final_top_k} items, L5: {self.l5_final_top_k} items")
    
    def get_retriever_name(self) -> str:
        """Get retriever name"""
        return "ComplexRetriever"
    
    async def run(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        Run complex retrieval (LangGraph node standard interface)
        
        Implement bottom-up strategy:
        1. L1 hybrid retrieval (top5) - factual foundation
        2. Session scoring analysis
        3. Deep association: L5 monthly profile of best session
        4. Intelligent intermediate layer supplement (based on question characteristics)
        5. Importance-weighted sorting
        
        Args:
            state: workflow state dictionary
            
        Returns:
            updated state dictionary
        """
        try:
            start_time = time.time()
            self.logger.info("🚀 Start ComplexRetriever retrieval process")
            
            # Validate input
            question = state.get("question", "").strip()
            if not question:
                error_msg = "Question cannot be empty"
                self.logger.error(error_msg)
                state["errors"] = state.get("errors", []) + [error_msg]
                return state
            
            llm_keywords = state.get("key_entities", [])
            self.logger.info(f"Get LLM keywords: {llm_keywords}")
            
            # 🔧 Check if L1 is in enabled_layers
            if 'L1' not in self.enabled_layers:
                self.logger.warning(f"⚠️ L1 not in enabled_layers ({self.enabled_layers}), skip L1 retrieval, execute direct high-level retrieval")
                # Directly retrieve memories in enabled_layers (no L1 starting point and bottom-up)
                return await self._direct_high_level_retrieval(state, llm_keywords)
            
            # Step 1: L1 hybrid retrieval (maintain factual foundation) - coarse then fine ranking
            self.logger.info("🔍 Step 1: L1 hybrid retrieval (factual foundation, coarse + fine ranking)")
            l1_results = await self.perform_l1_retrieval(
                state, llm_keywords, 
                top_k=self.l1_final_top_k,  # Final return count
                coarse_ranking=True  # Enable coarse ranking mechanism
            )
            
            if not l1_results:
                error_msg = "L1 retrieval returned no results"
                self.logger.warning(error_msg)
                state["errors"] = state.get("errors", []) + [error_msg]
                state["needs_retry"] = True
                state["retry_reason"] = error_msg
                return state
            
            self.logger.info(f"L1 retrieval obtained {len(l1_results)} results")
            
            # Step 2: Session scoring analysis
            self.logger.info("📊 Step 2: Session scoring analysis")
            session_analysis = self.analyze_l1_for_session(l1_results)
            
            best_session_id = session_analysis.get('best_session_id')
            from timem.utils.session_recall import limit_l1_to_top_sessions
            l1_results = limit_l1_to_top_sessions(
                l1_results, session_analysis.get("session_scores") or [], top_n=3
            )
            if not best_session_id:
                self.logger.warning("No valid session found, return only L1 results")
                final_results = self._importance_weighted_sort(l1_results)
                return self._build_final_result(state, final_results, session_analysis, time.time() - start_time)
            
            # Step 3: Multi-starting-point chain tracing to get parent memories (based on configured layers)
            self.logger.info(f"🔗 Step 3: Multi-starting-point chain tracing parent memories (layers: {self.enabled_layers})")
            
            # 🆕 Check if bottom-up mechanism is disabled
            if self.disable_bottom_up:
                self.logger.warning("⚠️ Bottom-up mechanism disabled, skip parent memory retrieval")
                parent_memories = {}
                target_levels = []
            else:
                # 🆕 Dynamically build target_levels and target_counts based on configured layers
                target_levels = [layer for layer in self.enabled_layers if layer != 'L1']  # Exclude L1 (already retrieved)
            target_counts = {}
            for layer in target_levels:
                if layer == 'L3':
                    target_counts[layer] = self.l3_final_top_k
                elif layer == 'L4':
                    target_counts[layer] = self.l4_final_top_k
                elif layer == 'L5':
                    target_counts[layer] = self.l5_final_top_k
                else:
                    # For other layers, read from final_limits
                    target_counts[layer] = self.strategy_config.get('final_limits', {}).get(layer, 1)
            
                self.logger.info(f"Target layers and counts: {target_counts}")
                
                # If no other layers need retrieval, skip this step
                if not target_levels:
                    self.logger.info("⏭️ Only L1 enabled, skip parent memory retrieval")
                    parent_memories = {}
                else:
                    # 🔧 Engineering-level fix: mandatory bottom-layer memory retrieval, throw exception on failure
                    try:
                        parent_memories = await self.get_parent_memories_by_chain(
                            l1_results, target_levels, target_counts, state
                        )
                    except Exception as e:
                        # Bottom-up already failed after 3 retries, this is a serious error
                        error_msg = f"❌ Bottom-up chain tracing completely failed (auto-retried 3 times): {e}"
                        self.logger.error(error_msg)
                        state["errors"] = state.get("errors", []) + [error_msg]
                        state["needs_retry"] = True
                        state["retry_reason"] = "bottom_up_chain_failed"
                        # 🔧 Critical fix: do not allow returning only L1, must mark as failed
                        raise Exception(error_msg)
            
            # 🔧 Engineering-level fix: dynamically validate retrieval results for each layer
            layer_stats = {}
            for layer in target_levels:
                layer_memories = parent_memories.get(layer, [])
                layer_stats[layer] = len(layer_memories)
                
                if not layer_memories and target_counts.get(layer, 0) > 0:
                    warning_msg = f"⚠️ Bottom-up failed to retrieve {layer} parent memories (expected {target_counts[layer]} items), database may lack parent-child relationships"
                    self.logger.warning(warning_msg)
                    state["warnings"] = state.get("warnings", []) + [warning_msg]
            
            if layer_stats:
                stats_str = ', '.join([f"{k}={v} items" for k, v in layer_stats.items()])
                self.logger.info(f"✅ Chain tracing completed: {stats_str}")
            
            # Validation: at least one layer should have memories
            if target_levels and all(len(parent_memories.get(layer, [])) == 0 for layer in target_levels):
                warning_msg = f"⚠️ Bottom-up failed to retrieve any parent memories ({'/'.join(target_levels)} all empty), results only contain L1"
                self.logger.warning(warning_msg)
                state["warnings"] = state.get("warnings", []) + [warning_msg]
            
            # 🔧 Fix: global deduplication logic, avoid qdrant_semantic and bottom_up duplication
            all_results = []
            seen_ids = set()
            duplicate_stats = {}
            
            # Add L1 memories (highest priority)
            for mem in l1_results:
                mem_id = mem.get('id') or mem.get('memory_id')
                if mem_id and mem_id not in seen_ids:
                    all_results.append(mem)
                    seen_ids.add(mem_id)
            
            # 🆕 Dynamically add parent memories for each layer (deduplication) (only when bottom-up not disabled)
            if not self.disable_bottom_up and target_levels:
                for layer in target_levels:
                    layer_memories = parent_memories.get(layer, [])
                    duplicate_stats[layer] = 0
                    
                    for memory in layer_memories:
                        mem_id = memory.get('id') or memory.get('memory_id')
                        if mem_id and mem_id not in seen_ids:
                            all_results.append(memory)
                            seen_ids.add(mem_id)
                        elif mem_id:
                            duplicate_stats[layer] += 1
                
                if sum(duplicate_stats.values()) > 0:
                    stats_str = ', '.join([f"{k} skipped {v} items" for k, v in duplicate_stats.items() if v > 0])
                    self.logger.info(f"⚠️ Deduplication stats: {stats_str}")
            else:
                if self.disable_bottom_up:
                    self.logger.info("⏭️ Bottom-up disabled, only use L1 memories")
            
            # Step 4: Importance-weighted sorting
            self.logger.info("⚠️ Step 4: Importance-weighted sorting")
            final_results = self._importance_weighted_sort(all_results)
            
            # Apply complex retrieval quality control
            final_results = self._apply_complex_quality_control(final_results, question)
            
            # Build final result
            execution_time = time.time() - start_time
            result = self._build_final_result(state, final_results, session_analysis, execution_time)
            
            self.logger.info(f"✅ ComplexRetriever completed: {len(final_results)} results, "
                           f"elapsed {execution_time:.2f}s")
            self.logger.info(f"Result distribution: {self._analyze_result_distribution(final_results)}")
            
            return result
            
        except Exception as e:
            error_msg = f"ComplexRetriever execution failed: {str(e)}"
            self.logger.error(error_msg)
            state["errors"] = state.get("errors", []) + [error_msg]
            return state
    
    def _analyze_question_needs(self, question: str) -> Dict[str, bool]:
        """
        Analyze question characteristics to decide which intermediate layers to supplement
        
        Args:
            question: user question
            
        Returns:
            requirement analysis result
        """
        question_lower = question.lower()
        
        # Session context requirement detection
        session_keywords = [
            "conversation", "said", "mentioned", "discussed", "talk", "exchange", 
            "conversation", "mentioned", "discussed", "talked"
        ]
        needs_session_context = any(keyword in question_lower for keyword in session_keywords)
        
        # Concept bridge requirement detection  
        concept_keywords = [
            "why", "how", "what", "reason", "mechanism", "principle", "concept", "understand",
            "why", "how", "mechanism", "concept", "understand", "reason"
        ]
        needs_conceptual_bridge = any(keyword in question_lower for keyword in concept_keywords)
        
        # If question is long and complex, more likely to need concept bridge
        if len(question) > 50 and ("?" in question or "？" in question):
            needs_conceptual_bridge = True
        
        result = {
            "needs_session_context": needs_session_context,
            "needs_conceptual_bridge": needs_conceptual_bridge
        }
        
        self.logger.info(f"Question requirement analysis: {result}")
        return result
    
    def _importance_weighted_sort(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Sort results by importance weight
        
        Sorting rules:
        1. L1 factual foundation - highest weight (1.0)
        2. L5 deep profile - second highest weight (0.8)
        3. L2 session context - medium weight (0.6)
        4. L3 concept bridge - lower weight (0.4)
        """
        try:
            # Set weights and calculate comprehensive scores
            weighted_results = []
            
            for result in results:
                level = result.get("level", "L1")
                original_score = result.get("fused_score", 0.5)
                
                # 🔧 Fix: set layer importance weights (consistent with complex retrieval strategy: L1+L2+L3+L4+L5)
                if level == "L1":
                    importance_weight = 1.0  # L1 factual foundation, highest weight
                elif level == "L5":
                    importance_weight = 0.8  # L5 deep profile, second highest weight
                elif level == "L2":
                    importance_weight = 0.7  # L2 session context, higher weight
                elif level == "L3":
                    importance_weight = 0.6  # L3 daily report memory, medium weight
                elif level == "L4":
                    importance_weight = 0.5  # L4 weekly report memory, medium weight
                else:
                    importance_weight = 0.3  # Other layers
                
                # Calculate comprehensive importance score
                result["importance_weight"] = importance_weight
                result["weighted_score"] = original_score * importance_weight
                
                weighted_results.append(result)
            
            # Sort by weighted score
            weighted_results.sort(key=lambda x: x.get("weighted_score", 0.0), reverse=True)
            
            # 🔧 Fix: sort by configured layer logic order: L1 → L2 → L3 → L4 → L5 (complex retrieval strategy)
            l1_results = [r for r in weighted_results if r.get("level") == "L1"]
            l2_results = [r for r in weighted_results if r.get("level") == "L2"]
            l3_results = [r for r in weighted_results if r.get("level") == "L3"]
            l4_results = [r for r in weighted_results if r.get("level") == "L4"]
            l5_results = [r for r in weighted_results if r.get("level") == "L5"]
            
            # Sort each layer by weighted score
            l1_results.sort(key=lambda x: x.get("weighted_score", 0.0), reverse=True)
            l2_results.sort(key=lambda x: x.get("weighted_score", 0.0), reverse=True)
            l3_results.sort(key=lambda x: x.get("weighted_score", 0.0), reverse=True)
            l4_results.sort(key=lambda x: x.get("weighted_score", 0.0), reverse=True)
            l5_results.sort(key=lambda x: x.get("weighted_score", 0.0), reverse=True)
            
            # Combine: L1 → L2 → L3 → L4 → L5 (consistent with complex retrieval strategy config)
            final_results = l1_results + l2_results + l3_results + l4_results + l5_results
            
            self.logger.info(f"Importance sorting completed: L1={len(l1_results)}, L2={len(l2_results)}, L3={len(l3_results)}, "
                           f"L4={len(l4_results)}, L5={len(l5_results)}")
            
            return final_results
            
        except Exception as e:
            self.logger.error(f"Importance sorting failed: {e}")
            return results
    
    def _apply_complex_quality_control(self, results: List[Dict[str, Any]], 
                                     question: str) -> List[Dict[str, Any]]:
        """
        Apply quality control for complex retrieval
        
        Ensure:
        1. L1 factual foundation is not diluted
        2. L5 deep profile provides valuable insights
        3. Intermediate layers truly supplement rather than interfere
        """
        try:
            controlled_results = []
            question_length = len(question)
            
            for result in results:
                level = result.get("level", "L1")
                content = result.get("content", "")
                title = result.get("title", "")
                
                # L1 factual foundation: always keep
                if level == "L1":
                    result["quality_score"] = 1.0
                    controlled_results.append(result)
                
                # L5 deep profile: bottom-up retrieval, must be included
                elif level == "L5":
                    result["quality_score"] = 0.9
                    controlled_results.append(result)
                    self.logger.debug(f"Keep L5 deep profile: {title[:30]}...")
                
                # L3 daily report memory: bottom-up retrieval, must be included  
                elif level == "L3":
                    result["quality_score"] = 0.6
                    controlled_results.append(result)
                    self.logger.debug(f"Keep L3 daily report: {title[:30]}...")
                
                # L4 weekly report memory: bottom-up retrieval, must be included
                elif level == "L4":
                    result["quality_score"] = 0.5
                    controlled_results.append(result)
                    self.logger.debug(f"Keep L4 weekly report: {title[:30]}...")
                
                # L2 session memory: bottom-up retrieval, must be included
                elif level == "L2":
                    result["quality_score"] = 0.7
                    controlled_results.append(result)
                    self.logger.debug(f"Keep L2 session: {title[:30]}...")
                
                else:
                    # Other layers
                    result["quality_score"] = 0.3
                    controlled_results.append(result)
            
            self.logger.info(f"Complex retrieval quality control: {len(results)} → {len(controlled_results)}")
            return controlled_results
            
        except Exception as e:
            self.logger.error(f"Quality control failed: {e}")
            return results
    
    def _analyze_result_distribution(self, results: List[Dict[str, Any]]) -> str:
        """Analyze result distribution"""
        try:
            level_counts = {}
            for result in results:
                level = result.get("level", "Unknown")
                level_counts[level] = level_counts.get(level, 0) + 1
            
            distribution = ", ".join([f"{level}={count}" for level, count in level_counts.items()])
            return distribution
        except:
            return "Analysis failed"
    
    def _build_final_result(self, original_state: Dict[str, Any], 
                          final_results: List[Dict[str, Any]],
                          session_analysis: Dict[str, Any],
                          execution_time: float) -> Dict[str, Any]:
        """Build final state result"""
        try:
            # Update original state
            updated_state = original_state.copy()
            
            # ✨ Explicitly preserve conversation mode config field (ensure LangGraph routing works correctly)
            # Unified use of return_memories_only as standard config item
            updated_state["return_memories_only"] = original_state.get("return_memories_only", False)
            
            # Add retrieval results
            updated_state["ranked_results"] = final_results
            updated_state["total_memories_searched"] = len(final_results)
            updated_state["retrieval_time"] = execution_time
            
            # Add session analysis information
            if session_analysis.get('best_session_id'):
                updated_state["best_session_id"] = session_analysis['best_session_id']
                updated_state["best_session_score"] = session_analysis['best_session_score']
                updated_state["session_analysis"] = session_analysis
                updated_state["temporal_info"] = session_analysis.get('temporal_info', {})
            
            # Add strategy performance metrics
            if "strategy_performance" not in updated_state:
                updated_state["strategy_performance"] = {}
            updated_state["strategy_performance"]["complex_retrieval"] = execution_time
            
            # Add retrieval metadata
            # Add retrieval metadata and layer statistics
            updated_state["retrieval_metadata"] = {
                "strategy_name": self.strategy_name,
                "strategy_description": self.strategy_config.get("description", ""),
                "time_associated": True,
                "session_based": True,
                "depth_analysis": True,
                "levels_included": list(set(r.get("level") for r in final_results if r.get("level"))),
                "intelligent_supplementing": True,
                "retrieved_counts": {
                    "L1": len([m for m in final_results if m.get("level") == "L1"]),
                    "L2": len([m for m in final_results if m.get("level") == "L2"]),
                    "L3": len([m for m in final_results if m.get("level") == "L3"]),
                    "L4": len([m for m in final_results if m.get("level") == "L4"]),
                    "L5": len([m for m in final_results if m.get("level") == "L5"]),
                }
            }
            
            # Ensure no error markers
            if not updated_state.get("errors"):
                updated_state["retrieval_success"] = True
            
            return updated_state
            
        except Exception as e:
            self.logger.error(f"Build final result failed: {e}")
            # Return basic result
            original_state["ranked_results"] = final_results
            original_state["errors"] = original_state.get("errors", []) + [f"Build result failed: {str(e)}"]
            return original_state
    
    async def _direct_high_level_retrieval(self, state: Dict[str, Any], 
                                           llm_keywords: List[str]) -> Dict[str, Any]:
        """
        Direct retrieval of high-level memories (L2-L5), without L1 starting point
        (Same implementation as SimpleRetriever/HybridRetriever)
        """
        import time
        start_time = time.time()
        
        self.logger.info(f"🎯 Direct high-level retrieval mode: {self.enabled_layers}")
        
        try:
            all_results = []
            seen_ids = set()
            
            # Iterate through each layer in enabled_layers
            for layer in self.enabled_layers:
                layer_limit = self.strategy_config.get('final_limits', {}).get(layer, 10)
                
                if layer_limit == 0:
                    self.logger.info(f"⏭️ Skip {layer} (limit=0)")
                    continue
                
                self.logger.info(f"🔍 Retrieve {layer} layer memories (target {layer_limit} items)")
                
                # Use hybrid retrieval
                layer_results = await self._retrieve_single_level(
                    state=state,
                    level=layer,
                    llm_keywords=llm_keywords,
                    top_k=layer_limit
                )
                
                # Deduplicate and add
                added = 0
                for mem in layer_results:
                    mem_id = mem.get('id') or mem.get('memory_id')
                    if mem_id and mem_id not in seen_ids:
                        all_results.append(mem)
                        seen_ids.add(mem_id)
                        added += 1
                
                self.logger.info(f"✅ {layer} retrieval completed: obtained {len(layer_results)} items, added {added} items (after deduplication)")
            
            # Sort by time
            final_results = sorted(all_results, key=lambda x: x.get('timestamp', ''), reverse=True)
            
            # Build result
            execution_time = time.time() - start_time
            session_analysis = {
                'best_session_id': None,
                'best_session_score': 0.0,
                'retrieval_mode': 'direct_high_level'
            }
            
            result = self._build_final_result(state, final_results, session_analysis, execution_time)
            
            self.logger.info(f"✅ Direct high-level retrieval completed: {len(final_results)} results, elapsed {execution_time:.2f}s")
            for layer in self.enabled_layers:
                layer_count = len([r for r in final_results if r.get('level') == layer])
                if layer_count > 0:
                    self.logger.info(f"   {layer}: {layer_count} items")
            
            return result
            
        except Exception as e:
            error_msg = f"Direct high-level retrieval failed: {str(e)}"
            self.logger.error(error_msg)
            state["errors"] = state.get("errors", []) + [error_msg]
            state["needs_retry"] = True
            state["retry_reason"] = error_msg
            return state
    
    async def _retrieve_single_level(self, state: Dict[str, Any], level: str, 
                                     llm_keywords: List[str], top_k: int) -> List[Dict[str, Any]]:
        """Retrieve memories of a single layer (using hybrid retrieval)"""
        try:
            # Convert state format
            retrieval_state = self._dict_to_state(state)
            
            # Directly call BM25 and semantic retrieval, then fuse (using correct parameters)
            bm25_results = await self._execute_bm25_search(
                retrieval_state,  # First parameter: RetrievalState
                llm_keywords,     # Second parameter: keywords list
                level             # Third parameter: layer
            )
            
            semantic_results = await self._execute_semantic_search(
                retrieval_state,  # First parameter: RetrievalState
                level             # Second parameter: layer
            )
            
            # Fuse results (using weighted fusion method)
            fused_results = await self._weighted_fusion(
                bm25_results, 
                semantic_results, 
                retrieval_state
            )
            
            return fused_results[:top_k]
            
        except Exception as e:
            self.logger.error(f"Retrieve {level} failed: {e}")
            return []
    
    def _create_empty_result(self, original_state: Dict[str, Any]) -> Dict[str, Any]:
        """Create empty result"""
        updated_state = original_state.copy()
        updated_state["ranked_results"] = []
        updated_state["total_memories_searched"] = 0
        updated_state["retrieval_time"] = 0.0
        updated_state["retrieval_success"] = False
        return updated_state


# Factory function
def create_complex_retriever(**kwargs) -> ComplexRetriever:
    """
    Factory function: create ComplexRetriever instance
    
    Args:
        **kwargs: parameters to pass to constructor
        
    Returns:
        ComplexRetriever instance
    """
    return ComplexRetriever(**kwargs)