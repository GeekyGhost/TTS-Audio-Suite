"""
ComfyUI Model Wrapper for TTS Audio Suite

This module provides wrappers that make TTS models compatible with ComfyUI's
model management system, enabling automatic memory management, "Clear VRAM" 
button functionality, and proper integration with ComfyUI's model lifecycle.
"""

import torch
import weakref
import gc
import time
from typing import Optional, Any, Dict, Union
from dataclasses import dataclass

# Global cache invalidation flag to force recreation of all engine instances
# When models are unloaded, this timestamp is updated to invalidate all node caches
_global_cache_invalidation_flag = 0.0


def is_engine_cache_valid(cache_timestamp: float) -> bool:
    """
    Check if an engine cache is still valid based on global invalidation flag.
    
    Args:
        cache_timestamp: When the cache entry was created
        
    Returns:
        True if cache is still valid, False if it should be invalidated
    """
    return cache_timestamp > _global_cache_invalidation_flag

# Import ComfyUI's model management if available
try:
    import comfy.model_management as model_management
    COMFYUI_AVAILABLE = True
except ImportError:
    # Fallback if ComfyUI not available
    COMFYUI_AVAILABLE = False
    model_management = None


@dataclass 
class ModelInfo:
    """Information about a model for memory management"""
    model: Any
    model_type: str  # "tts", "vc", "audio_separation", "hubert", etc.
    engine: str      # "chatterbox", "f5tts", "higgs_audio", "rvc", etc.
    device: str
    memory_size: int  # in bytes
    load_device: str


class ComfyUIModelWrapper:
    """
    Wrapper that makes TTS models compatible with ComfyUI's model management system.
    
    This allows TTS models to be automatically unloaded when VRAM is low,
    work with "Clear VRAM" buttons, and integrate properly with ComfyUI's ecosystem.
    """
    
    def __init__(self, model: Any, model_info: ModelInfo):
        """
        Initialize the wrapper.
        
        Args:
            model: The actual model instance (ChatterBox, F5-TTS, etc.)
            model_info: Metadata about the model
        """
        self.model = model
        self.model_info = model_info
        self.load_device = model_info.load_device
        self.current_device = model_info.device
        self._memory_size = model_info.memory_size
        
        # ComfyUI compatibility attributes
        # Convert device to torch.device object for ComfyUI compatibility
        device_name = model_info.device
        if device_name == "auto":
            device_name = "cuda" if torch.cuda.is_available() else "cpu"
        
        # ComfyUI expects torch.device objects, not strings
        if isinstance(device_name, str):
            if device_name == "cuda":
                self.device = torch.device("cuda", torch.cuda.current_device() if torch.cuda.is_available() else 0)
            else:
                self.device = torch.device(device_name)
        else:
            self.device = device_name
        self.dtype = getattr(model, 'dtype', torch.float32)
        self.offload_device = 'cpu'  # TTS models offload to CPU
        
        # ComfyUI compatibility attributes for diffusion models (TTS models don't need them)
        self.model_patches_models = []  # Empty list for TTS models
        self.parent = None              # TTS models don't have parent models
        
        # Additional ComfyUI LoadedModel compatibility attributes  
        # Use the same torch.device object for load_device
        self.load_device = self.device
        self.currently_used = True
        self.model_finalizer = None  # Will be set by LoadedModel
        self._patcher_finalizer = None
        
        # ComfyUI model patcher attributes (required for load_models_gpu)
        self.model_patches_to = {}  # Patch mapping for diffusion models (empty for TTS)
        self.model_options = {}     # Model loading options
        self.model_keys = set()     # Model state dict keys
        
        # Track if model is currently loaded on GPU
        self._is_loaded_on_gpu = self.current_device not in ['cpu', 'offload']
        
        # Track if model is valid for reuse (false if corrupted by CPU offloading)
        self._is_valid_for_reuse = True
        
        # Keep weak reference to avoid circular references
        self._model_ref = weakref.ref(model) if model is not None else None
        
    def loaded_size(self) -> int:
        """Return the memory size of the model in bytes"""
        size = self._memory_size if self._is_loaded_on_gpu else 0
        print(f"📊 TTS Model loaded_size query: {self.model_info.engine} {self.model_info.model_type} = {size // 1024 // 1024}MB (GPU: {self._is_loaded_on_gpu})")
        return size
        
    def model_size(self) -> int:
        """Return the total model size in bytes"""
        return self._memory_size
    
    def model_offloaded_memory(self) -> int:
        """Return the amount of memory that would be freed if offloaded"""
        return self.model_size() - self.loaded_size()
    
    def current_loaded_device(self) -> str:
        """Return the current device the model is loaded on"""
        print(f"📍 TTS Model current_loaded_device query: {self.model_info.engine} {self.model_info.model_type} = {self.current_device}")
        return self.current_device
    
    def partially_unload(self, device: str, memory_to_free: int) -> int:
        """
        Partially unload the model to free memory.
        
        For TTS models, this typically means moving to CPU or offloading.
        
        Args:
            device: Target device to move to (usually 'cpu')
            memory_to_free: Amount of memory to free in bytes
            
        Returns:
            Amount of memory actually freed in bytes
        """
        if not self._is_loaded_on_gpu:
            return 0
            
        model = self._model_ref() if self._model_ref else None
        if model is None:
            return 0
            
        freed_memory = 0
        
        try:
            # Move model to CPU if it has a .to() method
            if hasattr(model, 'to'):
                try:
                    # CRITICAL: Clear CUDA graphs before moving to CPU (prevents corruption)
                    self._clear_cuda_graphs(model)
                    
                    model.to('cpu')
                    freed_memory = self._memory_size
                    self.current_device = 'cpu'
                    self._is_loaded_on_gpu = False
                    print(f"🔄 Moved {self.model_info.model_type} model ({self.model_info.engine}) to CPU, freed {freed_memory // 1024 // 1024}MB")
                except Exception as e:
                    print(f"⚠️ Failed to move {self.model_info.model_type} model to CPU: {e}")
                    # Still mark as unloaded if the model reported an error moving to CPU
                    self.current_device = 'cpu'
                    self._is_loaded_on_gpu = False
                    freed_memory = self._memory_size
                
            # Handle nested models (like ChatterBox with multiple components)
            elif hasattr(model, '__dict__'):
                # CRITICAL: Clear CUDA graphs before moving to CPU (prevents corruption)
                self._clear_cuda_graphs(model)
                
                for attr_name, attr_value in model.__dict__.items():
                    if hasattr(attr_value, 'to') and hasattr(attr_value, 'parameters'):
                        try:
                            attr_value.to('cpu')
                            freed_memory += self._estimate_model_memory(attr_value)
                        except Exception as e:
                            print(f"⚠️ Failed to move {attr_name} to CPU: {e}")
                            pass
                            
                if freed_memory > 0:
                    self.current_device = 'cpu' 
                    self._is_loaded_on_gpu = False
                    print(f"🔄 Moved {self.model_info.model_type} model components ({self.model_info.engine}) to CPU, freed {freed_memory // 1024 // 1024}MB")
                    
        except Exception as e:
            print(f"⚠️ Failed to partially unload {self.model_info.model_type} model: {e}")
            
        # Force garbage collection after unloading
        if freed_memory > 0:
            gc.collect()
            # Be more careful with CUDA cache clearing to avoid interfering with CUDA graphs
            if torch.cuda.is_available():
                try:
                    torch.cuda.empty_cache()
                except Exception as e:
                    print(f"⚠️ CUDA cache clear warning (safe to ignore): {e}")
                
        return freed_memory
    
    def model_unload(self, memory_to_free: Optional[int] = None, unpatch_weights: bool = True) -> bool:
        """
        Fully unload the model from GPU memory.
        
        Args:
            memory_to_free: Amount of memory to free (ignored for full unload)
            unpatch_weights: Whether to unpatch weights (TTS models don't use this)
            
        Returns:
            True if model was unloaded, False otherwise
        """
        print(f"🔄 TTS Model unload requested: {self.model_info.engine} {self.model_info.model_type}")
        
        if memory_to_free is not None and memory_to_free < self.loaded_size():
            # Try partial unload first
            freed = self.partially_unload('cpu', memory_to_free)
            success = freed >= memory_to_free
            print(f"{'✅' if success else '❌'} Partial unload: freed {freed // 1024 // 1024}MB (requested {memory_to_free // 1024 // 1024}MB)")
            return success
            
        # Full unload
        freed = self.partially_unload('cpu', self._memory_size)
        success = freed > 0
        print(f"{'✅' if success else '❌'} Full unload: freed {freed // 1024 // 1024}MB")
        return success
    
    def _clear_cuda_graphs(self, model):
        """Clear CUDA graphs if the model supports it (prevents corruption when moving to CPU)"""
        try:
            print(f"🔍 Checking for CUDA graphs in {self.model_info.engine} model...")
            
            # Check if this is a Higgs Audio model with CUDA graphs
            if self.model_info.engine == "higgs_audio":
                print(f"🔍 Found Higgs Audio model, searching for decode_graph_runners...")
                
                # The CUDA graphs are nested deeper in the Higgs Audio model structure
                # Try to find them through various paths
                cuda_model = None
                
                # Path 1: Direct access
                if hasattr(model, 'decode_graph_runners'):
                    cuda_model = model
                    print(f"🔍 Found decode_graph_runners at top level")
                
                # Path 2: Through engine attribute
                elif hasattr(model, 'engine') and hasattr(model.engine, 'model') and hasattr(model.engine.model, 'decode_graph_runners'):
                    cuda_model = model.engine.model
                    print(f"🔍 Found decode_graph_runners in model.engine.model")
                
                # Path 3: Through model attribute
                elif hasattr(model, 'model') and hasattr(model.model, 'decode_graph_runners'):
                    cuda_model = model.model
                    print(f"🔍 Found decode_graph_runners in model.model")
                
                # Path 4: Search through all attributes recursively
                else:
                    print(f"🔍 Searching recursively for decode_graph_runners...")
                    def find_cuda_model(obj, depth=0, max_depth=3):
                        if depth > max_depth:
                            return None
                        if hasattr(obj, 'decode_graph_runners'):
                            return obj
                        if hasattr(obj, '__dict__'):
                            for attr_name, attr_value in obj.__dict__.items():
                                if not attr_name.startswith('_') and attr_value is not None:
                                    result = find_cuda_model(attr_value, depth + 1, max_depth)
                                    if result:
                                        print(f"🔍 Found decode_graph_runners in {attr_name} (depth {depth + 1})")
                                        return result
                        return None
                    
                    cuda_model = find_cuda_model(model)
                
                if cuda_model:
                    # Check for CUDA graphs but don't destroy them (causes corruption)
                    graph_count = sum(len(runners) for runners in cuda_model.decode_graph_runners.values())
                    if graph_count > 0:
                        print(f"🔍 Found {graph_count} CUDA graphs - leaving them intact to prevent corruption")
                        print(f"📝 CUDA graphs will be invalidated through cache management instead")
                    else:
                        print(f"📝 No CUDA graphs found in {self.model_info.engine} model")
                else:
                    print(f"⚠️ Could not locate decode_graph_runners in {self.model_info.engine} model structure")
            else:
                print(f"📝 Not a Higgs Audio model ({self.model_info.engine}), skipping CUDA graph cleanup")
                        
        except Exception as e:
            print(f"⚠️ Failed to clear CUDA graphs: {e}")
    
    def model_load(self, device: Optional[str] = None) -> None:
        """
        Load the model back to GPU.
        
        Args:
            device: Device to load to (defaults to original load_device)
        """
        if self._is_loaded_on_gpu:
            return
            
        target_device = device or self.load_device
        model = self._model_ref() if self._model_ref else None
        
        if model is None:
            return
            
        try:
            # Move model back to GPU
            if hasattr(model, 'to'):
                model.to(target_device)
                self.current_device = target_device
                self._is_loaded_on_gpu = True
                print(f"🔄 Moved {self.model_info.model_type} model ({self.model_info.engine}) back to {target_device}")
                
            # Handle nested models  
            elif hasattr(model, '__dict__'):
                for attr_name, attr_value in model.__dict__.items():
                    if hasattr(attr_value, 'to') and hasattr(attr_value, 'parameters'):
                        try:
                            attr_value.to(target_device)
                        except Exception:
                            pass
                            
                self.current_device = target_device
                self._is_loaded_on_gpu = True
                print(f"🔄 Moved {self.model_info.model_type} model components ({self.model_info.engine}) back to {target_device}")
                
        except Exception as e:
            print(f"⚠️ Failed to load {self.model_info.model_type} model to {target_device}: {e}")
    
    def is_clone(self, other) -> bool:
        """Check if this model is a clone of another model"""
        if not isinstance(other, ComfyUIModelWrapper):
            return False
        return (self.model_info.model_type == other.model_info.model_type and 
                self.model_info.engine == other.model_info.engine)
    
    def detach(self, unpatch_all: bool = False) -> None:
        """Detach the model - actually unload from GPU to CPU and invalidate cache"""
        print(f"🔧 TTS Model detach called: {self.model_info.engine} {self.model_info.model_type} (unpatch_all={unpatch_all})")
        
        # Actually unload the model from GPU
        freed = self.partially_unload('cpu', self._memory_size)
        if freed > 0:
            print(f"✅ TTS Model detached: freed {freed // 1024 // 1024}MB VRAM")
        else:
            print(f"⚠️ TTS Model detach: no memory freed (model may already be on CPU)")
        
        # CRITICAL: Mark model as invalid to prevent reuse of corrupted state
        # Models with CUDA graphs cannot be safely reused after CPU offloading
        self._is_valid_for_reuse = False
        print(f"🚫 Marked {self.model_info.engine} model as invalid for reuse (CUDA state corrupted)")
        
        # CRITICAL: Clear node-level engine caches to prevent reuse of corrupted engines
        # This is essential because TTS nodes have their own caching separate from ComfyUI wrapper cache
        self._clear_node_engine_caches()
    
    def _clear_node_engine_caches(self):
        """Clear engine caches in TTS nodes to prevent reuse of corrupted engines"""
        try:
            # Set global flag to invalidate all caches
            # This will be checked by nodes when they try to reuse cached engines
            global _global_cache_invalidation_flag
            _global_cache_invalidation_flag = time.time()
            print(f"🗑️ Set global cache invalidation flag to force engine recreation")
            
        except Exception as e:
            print(f"⚠️ Failed to clear node engine caches: {e}")
    
    def is_clone(self, other) -> bool:
        """Check if this model is a clone of another model"""
        return False  # TTS models don't support cloning
    
    def partially_load(self, device, extra_memory, force_patch_weights=False):
        """
        Partially load model to device (ComfyUI compatibility method)
        
        Args:
            device: Target device
            extra_memory: Extra memory available
            force_patch_weights: Whether to force patch weights
            
        Returns:
            Amount of memory used
        """
        print(f"🔄 TTS Model partially_load requested: {self.model_info.engine} {self.model_info.model_type} to {device}")
        
        # For TTS models, we either fully load or fully unload
        if device != 'cpu' and not self._is_loaded_on_gpu:
            self.model_load(device)
            return self._memory_size
        
        return 0  # No additional memory used
        
    @staticmethod
    def _estimate_model_memory(model) -> int:
        """Estimate memory usage of a PyTorch model"""
        if not hasattr(model, 'parameters'):
            return 0
            
        total_size = 0
        for param in model.parameters():
            total_size += param.nelement() * param.element_size()
        return total_size
    
    @staticmethod 
    def calculate_model_memory(model: Any) -> int:
        """Calculate total memory usage of a model in bytes"""
        if hasattr(model, 'parameters'):
            # PyTorch model
            return ComfyUIModelWrapper._estimate_model_memory(model)
        elif hasattr(model, '__dict__'):
            # Complex model with multiple components
            total_size = 0
            for attr_value in model.__dict__.values():
                if hasattr(attr_value, 'parameters'):
                    total_size += ComfyUIModelWrapper._estimate_model_memory(attr_value)
            return total_size
        else:
            # Estimate based on common model sizes
            return 1024 * 1024 * 1024  # Default 1GB estimate
    
    def __repr__(self):
        return f"ComfyUIModelWrapper({self.model_info.model_type}:{self.model_info.engine}, {self._memory_size // 1024 // 1024}MB, device={self.current_device})"


class ComfyUITTSModelManager:
    """
    Manager that integrates TTS models with ComfyUI's model management system.
    
    This replaces static caches with ComfyUI-managed model loading/unloading.
    """
    
    def __init__(self):
        self._model_cache: Dict[str, ComfyUIModelWrapper] = {}
        
    def load_model(self, 
                   model_factory_func, 
                   model_key: str,
                   model_type: str,
                   engine: str, 
                   device: str,
                   force_reload: bool = False,
                   **factory_kwargs) -> ComfyUIModelWrapper:
        """
        Load a model using ComfyUI's model management system.
        
        Args:
            model_factory_func: Function that creates the model
            model_key: Unique key for caching
            model_type: Type of model ("tts", "vc", etc.)  
            engine: Engine name ("chatterbox", "f5tts", etc.)
            device: Target device
            **factory_kwargs: Arguments for model factory function
            
        Returns:
            ComfyUI-wrapped model
        """
        # Check for resurrection opportunity (Higgs Audio in shadow storage)
        shadow_key = f"_shadow_{model_key}"
        if engine == "higgs_audio" and shadow_key in self._model_cache:
            print(f"👻 Resurrecting {engine} model from shadow storage (avoids CUDA corruption)")
            wrapper = self._model_cache.pop(shadow_key)  # Remove from shadow
            
            # Reset CUDA graph state for fresh initialization
            if hasattr(wrapper.model, 'engine') and hasattr(wrapper.model.engine, 'cuda_graphs_initialized'):
                wrapper.model.engine.cuda_graphs_initialized = False
                print(f"🔄 Reset CUDA graph state for resurrection")
            
            # Restore to active cache
            self._model_cache[model_key] = wrapper
            wrapper._is_valid_for_reuse = True
            print(f"✨ Successfully resurrected {engine} model from shadow storage")
            return wrapper
        
        # Check if already cached
        if model_key in self._model_cache and not force_reload:
            wrapper = self._model_cache[model_key]
            is_valid = getattr(wrapper, '_is_valid_for_reuse', True)
            print(f"🔍 Cache check for {model_type} ({engine}): valid={is_valid}, force_reload={force_reload}")
            
            # Check if cached model is still valid for reuse
            if not is_valid:
                # For Higgs Audio with CUDA graph corruption, try to reinitialize in-place
                if engine == "higgs_audio":
                    print(f"🔄 Attempting in-place reinitializion of corrupted {engine} model to avoid memory conflicts")
                    try:
                        # Reset CUDA graph state without creating new model
                        if hasattr(wrapper.model, 'engine') and hasattr(wrapper.model.engine, 'cuda_graphs_initialized'):
                            wrapper.model.engine.cuda_graphs_initialized = False
                            print(f"✅ Reset CUDA graph state for existing model")
                        
                        # Move back to GPU for reinit
                        wrapper.model_load(device)
                        # Mark as valid again
                        wrapper._is_valid_for_reuse = True
                        print(f"✅ Successfully reinitialized {engine} model in-place")
                        return wrapper
                    except Exception as e:
                        print(f"⚠️ In-place reinit failed: {e}, falling back to full recreation")
                
                print(f"🗑️ Removing invalid cached model: {model_type} ({engine}) - corrupted by previous unload")
                self.remove_model(model_key)
                # Continue to create new model below
            else:
                print(f"♻️ Reusing valid cached model: {model_type} ({engine})")
                # Ensure model is loaded on correct device
                if wrapper.current_device != device and device != 'auto':
                    wrapper.model_load(device)
                return wrapper
        elif force_reload and model_key in self._model_cache:
            wrapper = self._model_cache[model_key]
            
            # For Higgs Audio, try in-place reinitialization instead of full recreation
            if engine == "higgs_audio":
                print(f"🔄 Force reload: attempting in-place reinitializion of {engine} model to avoid memory conflicts")
                try:
                    # Reset CUDA graph state without creating new model
                    if hasattr(wrapper.model, 'engine') and hasattr(wrapper.model.engine, 'cuda_graphs_initialized'):
                        wrapper.model.engine.cuda_graphs_initialized = False
                        print(f"✅ Reset CUDA graph state for existing model")
                    
                    # Move back to GPU for reinit
                    wrapper.model_load(device)
                    # Mark as valid again
                    wrapper._is_valid_for_reuse = True
                    print(f"✅ Successfully reinitialized {engine} model in-place (force reload)")
                    return wrapper
                except Exception as e:
                    print(f"⚠️ Force reload in-place reinit failed: {e}, falling back to full recreation")
            
            print(f"🔄 Force reloading {model_type} ({engine}) - removing from cache")
            self.remove_model(model_key)
            
        # Aggressive memory management before loading new model
        if COMFYUI_AVAILABLE and model_management is not None and device != 'cpu':
            try:
                # Free up memory aggressively - request 3GB to ensure space for new model
                if hasattr(model_management, 'free_memory') and callable(getattr(model_management, 'free_memory', None)):
                    if hasattr(model_management, 'get_torch_device'):
                        torch_device = model_management.get_torch_device()
                        # Request 3GB of free VRAM (aggressive cleanup for TTS models)
                        memory_freed = model_management.free_memory(3 * 1024 * 1024 * 1024, torch_device)
                        if memory_freed and memory_freed > 0:
                            print(f"🧹 Freed {memory_freed // 1024 // 1024}MB VRAM for new {model_type} model")
                
                # Also try manual cleanup of our own TTS model cache
                # Clear models from other engines to make room
                if model_type == "tts" and engine != "":
                    # Get current cache stats
                    cached_models = list(self._model_cache.keys())
                    models_to_clear = []
                    
                    for cache_key in cached_models:
                        wrapper = self._model_cache[cache_key]
                        # Clear models from different engines to free VRAM
                        if wrapper.model_info.engine != engine and wrapper.model_info.model_type == "tts":
                            models_to_clear.append(cache_key)
                    
                    if models_to_clear:
                        print(f"🗑️ Clearing {len(models_to_clear)} TTS models from other engines to free VRAM...")
                        for key in models_to_clear:
                            self.remove_model(key)
                            
            except Exception as e:
                # Silently ignore memory management errors to avoid spam
                pass
        
        # Create the model
        print(f"🔧 Creating new {model_type} model ({engine}) on {device} - fresh instance after cache invalidation")
        
        # Higgs Audio now uses deferred CUDA graph initialization to prevent corruption
        if device.startswith('cuda') and engine == "higgs_audio":
            print(f"📝 Creating fresh {engine} model (CUDA graphs deferred until first inference)")
            import gc
            gc.collect()
        
        # Ensure device parameter is available to factory function
        factory_kwargs['device'] = device
        model = model_factory_func(**factory_kwargs)
        
        # Calculate memory usage
        memory_size = ComfyUIModelWrapper.calculate_model_memory(model)
        
        # Create model info
        model_info = ModelInfo(
            model=model,
            model_type=model_type,
            engine=engine,
            device=device,
            memory_size=memory_size,
            load_device=device
        )
        
        # Wrap for ComfyUI
        wrapper = ComfyUIModelWrapper(model, model_info)
        
        # Cache the wrapper
        self._model_cache[model_key] = wrapper
        
        # Register with ComfyUI using the proper load_models_gpu method
        if COMFYUI_AVAILABLE and model_management is not None:
            # Try the safer manual approach first since load_models_gpu seems to have issues
            try:
                # Manually add to current_loaded_models using LoadedModel (ComfyUI's internal approach)
                if hasattr(model_management, 'LoadedModel') and hasattr(model_management, 'current_loaded_models'):
                    loaded_model = model_management.LoadedModel(wrapper)
                    if model is not None:
                        loaded_model.real_model = weakref.ref(model)
                        # Set up the finalizer that ComfyUI expects
                        if hasattr(model_management, 'cleanup_models'):
                            loaded_model.model_finalizer = weakref.finalize(model, model_management.cleanup_models)
                        else:
                            # Create a dummy finalizer that doesn't crash
                            loaded_model.model_finalizer = weakref.finalize(model, lambda: None)
                    
                    # Keep a strong reference to our wrapper to prevent garbage collection
                    # This ensures LoadedModel.model property doesn't return None
                    loaded_model._tts_wrapper_ref = wrapper
                    
                    model_management.current_loaded_models.insert(0, loaded_model)  # Insert at 0 like ComfyUI does
                    total_models = len(model_management.current_loaded_models)
                    print(f"✅ Registered {model_type} model with ComfyUI model management via manual LoadedModel (#{total_models})")
                else:
                    print(f"⚠️ ComfyUI LoadedModel or current_loaded_models not available")
            except Exception as e:
                print(f"⚠️ Failed to register with ComfyUI model management: {e}")
                
        return wrapper
    
    def get_model(self, model_key: str) -> Optional[ComfyUIModelWrapper]:
        """Get a cached model by key"""
        return self._model_cache.get(model_key)
        
    def remove_model(self, model_key: str) -> bool:
        """Remove a model from cache and ComfyUI tracking (with special handling for Higgs Audio)"""
        if model_key in self._model_cache:
            wrapper = self._model_cache[model_key]
            
            # Never actually destroy Higgs Audio models - hide them in shadow storage
            if wrapper.model_info.engine == "higgs_audio":
                print(f"🫥 Moving {wrapper.model_info.engine} model to shadow storage (prevents CUDA corruption)")
                shadow_key = f"_shadow_{model_key}"
                
                # Remove from active cache but store in shadow
                self._model_cache.pop(model_key)
                self._model_cache[shadow_key] = wrapper
                
                # Remove from ComfyUI tracking to "free" memory
                if COMFYUI_AVAILABLE and model_management is not None:
                    try:
                        if hasattr(model_management, 'current_loaded_models'):
                            if wrapper in model_management.current_loaded_models:
                                model_management.current_loaded_models.remove(wrapper)
                                print(f"🗑️ Removed from ComfyUI tracking (hidden in shadow)")
                    except Exception as e:
                        print(f"⚠️ Failed to remove from ComfyUI tracking: {e}")
                
                # Don't actually unload - keep model alive
                print(f"📦 {wrapper.model_info.engine} model preserved in shadow storage")
                return True
            else:
                # Normal destruction for other engines  
                wrapper = self._model_cache.pop(model_key)
                
                # Remove from ComfyUI tracking if available
                if COMFYUI_AVAILABLE and model_management is not None:
                    try:
                        if hasattr(model_management, 'current_loaded_models'):
                            if wrapper in model_management.current_loaded_models:
                                model_management.current_loaded_models.remove(wrapper)
                                print(f"🗑️ Removed model from ComfyUI tracking")
                    except Exception as e:
                        print(f"⚠️ Failed to remove from ComfyUI tracking: {e}")
                
                # Unload from GPU
                wrapper.model_unload()
                return True
        return False
        
    def clear_cache(self, model_type: Optional[str] = None, engine: Optional[str] = None):
        """Clear cached models with optional filtering"""
        keys_to_remove = []
        
        for key, wrapper in self._model_cache.items():
            should_remove = True
            
            if model_type and wrapper.model_info.model_type != model_type:
                should_remove = False
            if engine and wrapper.model_info.engine != engine:  
                should_remove = False
                
            if should_remove:
                keys_to_remove.append(key)
                
        for key in keys_to_remove:
            self.remove_model(key)
            
        print(f"🧹 Cleared {len(keys_to_remove)} models from cache")
        
    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics"""
        total_memory = sum(w.loaded_size() for w in self._model_cache.values())
        by_type = {}
        by_engine = {}
        
        for wrapper in self._model_cache.values():
            model_type = wrapper.model_info.model_type
            engine = wrapper.model_info.engine
            
            by_type[model_type] = by_type.get(model_type, 0) + 1
            by_engine[engine] = by_engine.get(engine, 0) + 1
            
        return {
            'total_models': len(self._model_cache),
            'total_memory_mb': total_memory // 1024 // 1024,
            'by_type': by_type,
            'by_engine': by_engine,
            'comfyui_integration': COMFYUI_AVAILABLE
        }


# Global instance for all TTS models
tts_model_manager = ComfyUITTSModelManager()