"""
Character Parser for ChatterBox Voice - Universal Text Processing
Handles character tag parsing for both F5TTS and ChatterBox TTS nodes
"""

import re
from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional, Union
from pathlib import Path


@dataclass
class CharacterSegment:
    """Represents a single text segment with character assignment and language"""
    character: str
    text: str
    start_pos: int
    end_pos: int
    language: Optional[str] = None
    original_character: Optional[str] = None  # Original character before alias resolution
    
    def __str__(self) -> str:
        lang_info = f", lang='{self.language}'" if self.language else ""
        orig_info = f", orig='{self.original_character}'" if self.original_character and self.original_character != self.character else ""
        return f"CharacterSegment(character='{self.character}'{orig_info}{lang_info}, text='{self.text[:50]}...', pos={self.start_pos}-{self.end_pos})"


class CharacterParser:
    """
    Universal character parsing system for both F5TTS and ChatterBox TTS.
    
    Features:
    - Parse [CharacterName] and [language:CharacterName] tags from text
    - Split text into character-specific segments with language awareness
    - Robust fallback system for missing characters
    - Support for both single text and SRT subtitle processing
    - Compatible with voice folder structure
    - Language-aware character switching
    """
    
    # Regex pattern for character tags: [CharacterName] or [language:CharacterName] (excludes pause tags)
    CHARACTER_TAG_PATTERN = re.compile(r'\[(?!(?:pause|wait|stop):)([^\]]+)\]')
    
    # Regex to parse language:character format (supports flexible language names)
    LANGUAGE_CHARACTER_PATTERN = re.compile(r'^([a-zA-Z0-9\-_À-ÿ\s]+):(.*)$')
    
    def __init__(self, default_character: str = "narrator", default_language: Optional[str] = None):
        """
        Initialize character parser.
        
        Args:
            default_character: Default character name for untagged text
            default_language: Default language for characters without explicit language
        """
        self.default_character = default_character
        self.default_language = default_language or "en"
        self.available_characters = set()
        self.character_fallbacks = {}
        self.character_language_defaults = {}
        
        # Cache for character language resolution to prevent duplicate logging
        self._character_language_cache = {}
        self._logged_characters = set()
        self._logged_character_warnings = set()
        
        # Language alias system for flexible language switching
        self.language_aliases = {
            # German variations
            'de': 'de', 'german': 'de', 'deutsch': 'de', 'germany': 'de', 'deutschland': 'de',
            
            # English variations
            'en': 'en', 'english': 'en', 'eng': 'en', 'usa': 'en', 'uk': 'en', 'america': 'en', 'britain': 'en',
            
            # Brazilian Portuguese (separate from European Portuguese)
            'pt-br': 'pt-br', 'ptbr': 'pt-br', 'brazilian': 'pt-br', 'brasilian': 'pt-br',
            'brazil': 'pt-br', 'brasil': 'pt-br', 'br': 'pt-br', 'português brasileiro': 'pt-br',
            
            # European Portuguese (separate from Brazilian)
            'pt-pt': 'pt-pt', 'portugal': 'pt-pt', 'european portuguese': 'pt-pt',
            'portuguese': 'pt-pt', 'português': 'pt-pt', 'portugues': 'pt-pt',
            
            # French variations
            'fr': 'fr', 'french': 'fr', 'français': 'fr', 'francais': 'fr', 
            'france': 'fr', 'français de france': 'fr',
            
            # Spanish variations
            'es': 'es', 'spanish': 'es', 'español': 'es', 'espanol': 'es',
            'spain': 'es', 'españa': 'es', 'castilian': 'es',
            
            # Italian variations
            'it': 'it', 'italian': 'it', 'italiano': 'it', 'italy': 'it', 'italia': 'it',
            
            # Norwegian variations
            'no': 'no', 'norwegian': 'no', 'norsk': 'no', 'norway': 'no', 'norge': 'no',
            
            # Dutch variations
            'nl': 'nl', 'dutch': 'nl', 'nederlands': 'nl', 'netherlands': 'nl', 'holland': 'nl',
            
            # Japanese variations
            'ja': 'ja', 'japanese': 'ja', '日本語': 'ja', 'japan': 'ja', 'nihongo': 'ja',
            
            # Chinese variations
            'zh': 'zh', 'chinese': 'zh', '中文': 'zh', 'china': 'zh',
            'zh-cn': 'zh-cn', 'mandarin': 'zh-cn', 'simplified': 'zh-cn', 'mainland': 'zh-cn',
            'zh-tw': 'zh-tw', 'traditional': 'zh-tw', 'taiwan': 'zh-tw', 'taiwanese': 'zh-tw',
            
            # Russian variations
            'ru': 'ru', 'russian': 'ru', 'русский': 'ru', 'russia': 'ru', 'россия': 'ru',
            
            # Korean variations
            'ko': 'ko', 'korean': 'ko', '한국어': 'ko', 'korea': 'ko', 'south korea': 'ko',
            
            # Indian Languages (F5-Hindi-Small for Hindi, others use base F5TTS models)
            
            # Hindi variations
            'hi': 'hi', 'hindi': 'hi', 'हिन्दी': 'hi', 'hin': 'hi', 'देवनागरी': 'hi',
            
            # Assamese variations
            'as': 'as', 'assamese': 'as', 'অসমীয়া': 'as', 'asom': 'as', 'axomiya': 'as',
            
            # Bengali variations  
            'bn': 'bn', 'bengali': 'bn', 'বাংলা': 'bn', 'bangla': 'bn', 'west bengal': 'bn',
            'bangladesh': 'bn', 'bengal': 'bn',
            
            # Gujarati variations
            'gu': 'gu', 'gujarati': 'gu', 'ગુજરાતી': 'gu', 'gujarat': 'gu', 'gujrati': 'gu',
            
            # Kannada variations
            'kn': 'kn', 'kannada': 'kn', 'ಕನ್ನಡ': 'kn', 'karnataka': 'kn', 'kanarese': 'kn',
            
            # Malayalam variations
            'ml': 'ml', 'malayalam': 'ml', 'മലയാളം': 'ml', 'kerala': 'ml', 'malayali': 'ml',
            
            # Marathi variations
            'mr': 'mr', 'marathi': 'mr', 'मराठी': 'mr', 'maharashtra': 'mr',
            
            # Odia variations
            'or': 'or', 'odia': 'or', 'ଓଡ଼ିଆ': 'or', 'oriya': 'or', 'odisha': 'or', 'orissa': 'or',
            
            # Punjabi variations
            'pa': 'pa', 'punjabi': 'pa', 'ਪੰਜਾਬੀ': 'pa', 'panjabi': 'pa', 'punjab': 'pa',
            
            # Tamil variations
            'ta': 'ta', 'tamil': 'ta', 'தமிழ்': 'ta', 'tamil nadu': 'ta', 'tamilnadu': 'ta',
            
            # Telugu variations
            'te': 'te', 'telugu': 'te', 'తెలుగు': 'te', 'andhra pradesh': 'te',
            'andhra': 'te', 'telangana': 'te',
        }
    
    def resolve_language_alias(self, language_input: str) -> str:
        """
        Resolve language alias to canonical language code.
        
        Args:
            language_input: User input language (e.g., "German", "brasil", "pt-BR")
            
        Returns:
            Canonical language code (e.g., "de", "pt-br")
        """
        # Normalize input: lowercase and strip whitespace
        normalized = language_input.strip().lower()
        
        # Look up in aliases
        canonical = self.language_aliases.get(normalized)
        if canonical:
            return canonical
            
        # If no alias found, return the original (for backward compatibility)
        return normalized
    
    def set_available_characters(self, characters: List[str]):
        """
        Set list of available character voices.
        
        Args:
            characters: List of character names that have voice references
        """
        self.available_characters = set(char.lower() for char in characters)
    
    def add_character_fallback(self, character: str, fallback: str):
        """
        Add a fallback mapping for a character.
        
        Args:
            character: Character name that might not exist
            fallback: Character name to use as fallback
        """
        self.character_fallbacks[character.lower()] = fallback.lower()
    
    def set_character_language_default(self, character: str, language: str):
        """
        Set default language for a character.
        
        Args:
            character: Character name
            language: Default language code (e.g., 'en', 'de', 'fr')
        """
        self.character_language_defaults[character.lower()] = language.lower()
    
    def parse_language_character_tag(self, tag_content: str) -> Tuple[Optional[str], str]:
        """
        Parse character tag content to extract language and character.
        
        Args:
            tag_content: Content inside character brackets (e.g., "Alice" or "de:Alice")
            
        Returns:
            Tuple of (language, character_name) where language can be None
        """
        # Check if it's in language:character format
        match = self.LANGUAGE_CHARACTER_PATTERN.match(tag_content.strip())
        if match:
            raw_language = match.group(1)
            character = match.group(2).strip()
            # Resolve language alias to canonical form
            language = self.resolve_language_alias(raw_language)
            # If character is empty (e.g., [fr:]), default to narrator
            if not character:
                character = self.default_character
            return language, character
        else:
            # Just a character name, no explicit language
            return None, tag_content.strip()
    
    def resolve_character_language(self, character: str, explicit_language: Optional[str] = None) -> str:
        """
        Resolve the language to use for a character with caching to prevent log spam.
        
        Priority:
        1. Explicitly provided language (from [lang:character] tag)
        2. Character's default language (from alias system)
        3. Global default language
        
        Args:
            character: Character name
            explicit_language: Language explicitly specified in tag
            
        Returns:
            Language code to use (normalized, no local: prefix)
        """
        if explicit_language:
            # Normalize explicit language immediately
            if explicit_language.startswith("local:"):
                explicit_language = explicit_language[6:]
            return explicit_language
        
        character_lower = character.lower()
        
        # Check cache first to avoid repeated lookups and logging
        cache_key = character_lower
        if cache_key in self._character_language_cache:
            return self._character_language_cache[cache_key]
        
        resolved_language = None
        
        # Priority 1: Character language defaults (internal cache)
        if character_lower in self.character_language_defaults:
            resolved_language = self.character_language_defaults[character_lower]
            # Normalize alias language
            if resolved_language and resolved_language.startswith("local:"):
                resolved_language = resolved_language[6:]
            # Only log once per character
            if character_lower not in self._logged_characters:
                print(f"🎭 Character '{character}' auto-switching to 🚨 alias default language '{resolved_language}'")
                self._logged_characters.add(character_lower)
        
        # Priority 2: Check voice discovery system for language defaults
        if not resolved_language:
            try:
                from utils.voice.discovery import voice_discovery
                alias_language = voice_discovery.get_character_default_language(character_lower)
                if alias_language:
                    resolved_language = alias_language
                    # Normalize alias language from voice discovery
                    if resolved_language and resolved_language.startswith("local:"):
                        resolved_language = resolved_language[6:]
                    # Only log once per character
                    if character_lower not in self._logged_characters:
                        print(f"🎭 Character '{character}' auto-switching to 🚨 alias default language '{resolved_language}'")
                        self._logged_characters.add(character_lower)
                # Remove spam: don't log "has no language default" for every character
            except Exception:
                pass  # Silently handle voice discovery errors
        
        # Priority 3: Fall back to global default
        if not resolved_language:
            resolved_language = self.default_language
        
        # Normalize local: prefix for consistency
        if resolved_language and resolved_language.startswith("local:"):
            resolved_language = resolved_language[6:]
        
        # Cache the result
        self._character_language_cache[cache_key] = resolved_language
        return resolved_language
    
    def reset_session_cache(self):
        """Reset caches for a new parsing session to allow fresh logging."""
        self._character_language_cache.clear()
        self._logged_characters.clear()
        self._logged_character_warnings.clear()
    
    def get_character_language_summary(self) -> str:
        """
        Get a consolidated summary of character language mappings for logging.
        
        Returns:
            Formatted summary string of character→language mappings
        """
        if not self._character_language_cache:
            return ""
        
        # Group characters by language
        lang_groups = {}
        for char, lang in self._character_language_cache.items():
            if lang not in lang_groups:
                lang_groups[lang] = []
            lang_groups[lang].append(char)
        
        # Format as: Alice(de), Bob(fr→en fallback), Others(en)
        summary_parts = []
        for lang, chars in sorted(lang_groups.items()):
            if len(chars) == 1:
                summary_parts.append(f"{chars[0]}({lang})")
            else:
                summary_parts.append(f"{', '.join(chars)}({lang})")
        
        return ', '.join(summary_parts)
    
    def set_engine_aware_default_language(self, model_or_language: str, engine_type: str):
        """
        Set default language based on engine model or language code.
        
        Args:
            model_or_language: Either a model name (F5-TTS) or language code (ChatterBox)
            engine_type: Engine type ("f5tts" or "chatterbox")
        """
        # ChatterBox passes language codes directly, F5-TTS passes model names
        if engine_type == "chatterbox":
            # ChatterBox gives us language names (with possible local: prefix)
            # Need to normalize to language codes
            normalized_language = self._normalize_chatterbox_language(model_or_language)
            self.default_language = normalized_language
            # print(f"🔧 Character Parser: Default language set to '{normalized_language}' for ChatterBox (from '{model_or_language}')")
        else:
            # F5-TTS gives us model names, need to infer language
            inferred_language = self._infer_language_from_engine_model(model_or_language, engine_type)
            if inferred_language:
                self.default_language = inferred_language
                # print(f"🔧 Character Parser: Default language set to '{inferred_language}' based on F5-TTS model '{model_or_language}'")
    
    def _infer_language_from_engine_model(self, model_name: str, engine_type: str) -> Optional[str]:
        """
        Infer language code from engine model name.
        
        Args:
            model_name: Model name (e.g., "F5-PT-BR", "F5TTS_Base")
            engine_type: Engine type ("f5tts" or "chatterbox")
            
        Returns:
            Inferred language code or None if can't infer
        """
        try:
            # Use the existing language mapper system - much cleaner and flexible!
            from utils.models.language_mapper import get_language_mapper
            
            mapper = get_language_mapper(engine_type)
            mappings = mapper.get_all_mappings().get(engine_type, {})
            
            # Reverse lookup: find language code that maps to this model
            for lang_code, mapped_model in mappings.items():
                if mapped_model == model_name:
                    return lang_code
            
            # Fallback for base models that aren't in specific language mappings
            if engine_type == "f5tts" and any(x in model_name.lower() for x in ['f5tts_base', 'f5tts_v1_base', 'e2tts_base']):
                return 'en'
            elif engine_type == "chatterbox" and 'english' in model_name.lower():
                return 'en'
                
        except ImportError:
            # Fallback if language mapper not available - shouldn't happen but just in case
            pass
        
        return None  # Can't infer language
    
    def _normalize_chatterbox_language(self, language_input: str) -> str:
        """
        Normalize ChatterBox language input to canonical language code.
        
        Args:
            language_input: ChatterBox language (e.g., "German", "local:German", "de")
            
        Returns:
            Normalized language code (local: prefix removed for consistency)
        """
        # Remove local: prefix for consistency - model loading will still use local models
        if language_input.startswith("local:"):
            language_input = language_input[6:]
        
        # Map ChatterBox model names to language codes
        chatterbox_language_map = {
            "english": "en",
            "german": "de", 
            "norwegian": "no"
        }
        
        # Check if it's a ChatterBox model name
        normalized = language_input.lower()
        if normalized in chatterbox_language_map:
            return chatterbox_language_map[normalized]
        
        # If it's already a language code or unknown, return as-is
        return normalized
    
    def normalize_character_name(self, character_name: str, skip_narrator_alias: bool = False) -> str:
        """
        Normalize character name and apply alias resolution and fallback if needed.
        
        Args:
            character_name: Raw character name from tag
            skip_narrator_alias: If True, skip alias resolution for "narrator" character
            
        Returns:
            Normalized character name or fallback
        """
        # Clean and normalize
        normalized = character_name.strip().lower()
        
        # Remove common punctuation from character names
        normalized = re.sub(r'[：:,，]', '', normalized)
        
        # First, try to resolve through alias system
        # Skip alias resolution for "narrator" when it comes from language-only tags
        # This preserves user's narrator voice priority (opt_narrator > dropdown > character map)
        if not (skip_narrator_alias and normalized == "narrator"):
            try:
                from utils.voice.discovery import voice_discovery
                resolved = voice_discovery.resolve_character_alias(normalized)
                if resolved != normalized:
                    # print(f"🗂️ Character Parser: '{character_name}' → '{resolved}' (alias)")
                    normalized = resolved
            except Exception as e:
                # If alias resolution fails, continue with original name
                pass
        
        # Check if character is available
        if normalized in self.available_characters:
            return normalized
        
        # Check fallback mapping
        if normalized in self.character_fallbacks:
            fallback = self.character_fallbacks[normalized]
            print(f"🔄 Character Parser: '{character_name}' → '{fallback}' (fallback)")
            return fallback
        
        # Default fallback - only log once per character per session
        if character_name not in self._logged_character_warnings:
            print(f"⚠️ Character Parser: Character '{character_name}' not found, using '{self.default_character}'")
            self._logged_character_warnings.add(character_name)
        return self.default_character
    
    def parse_text_segments(self, text: str) -> List[CharacterSegment]:
        """
        Parse text into character-specific segments with proper line-by-line processing.
        
        Args:
            text: Input text with [Character] tags
            
        Returns:
            List of CharacterSegment objects
        """
        segments = []
        
        # Split text by lines and process each line completely independently
        lines = text.split('\n')
        global_pos = 0
        
        for line in lines:
            line_start_pos = global_pos
            original_line = line
            line = line.strip()
            
            if not line:
                global_pos += len(original_line) + 1  # Account for empty line + newline
                continue
            
            # Each line is processed independently - no character state carries over
            line_segments = self._parse_single_line(line, line_start_pos)
            segments.extend(line_segments)
            
            global_pos += len(original_line) + 1  # +1 for newline
        
        # If no segments were created, treat entire text as default character
        if not segments and text.strip():
            segments.append(CharacterSegment(
                character=self.default_character,
                text=text.strip(),
                start_pos=0,
                end_pos=len(text),
                language=self.resolve_character_language(self.default_character),
                original_character=self.default_character
            ))
        
        return segments
    
    def _parse_single_line(self, line: str, line_start_pos: int) -> List[CharacterSegment]:
        """
        Parse a single line for character tags, treating it completely independently.
        
        Args:
            line: Single line of text (no newlines)
            line_start_pos: Starting position of this line in the original text
            
        Returns:
            List of CharacterSegment objects for this line only
        """
        segments = []
        current_pos = 0
        current_character = self.default_character
        current_language = self.default_language
        
        # IMPORTANT: Each line starts fresh with narrator as default
        # If the line doesn't start with a character tag, everything is narrator
        
        # Quick check: if line doesn't contain any character tags, it's all narrator
        if not self.CHARACTER_TAG_PATTERN.search(line):
            if line.strip():
                segments.append(CharacterSegment(
                    character=self.default_character,
                    text=line.strip(),
                    start_pos=line_start_pos,
                    end_pos=line_start_pos + len(line),
                    language=self.resolve_character_language(self.default_character),
                    original_character=self.default_character
                ))
            return segments
        
        # Find all character tags in this line
        for match in self.CHARACTER_TAG_PATTERN.finditer(line):
            # Add text before this tag (if any) with current character (narrator)
            before_tag = line[current_pos:match.start()].strip()
            if before_tag:
                segments.append(CharacterSegment(
                    character=current_character,
                    text=before_tag,
                    start_pos=line_start_pos + current_pos,
                    end_pos=line_start_pos + match.start(),
                    language=current_language,
                    original_character=current_character  # Before this tag, it's already resolved
                ))
            
            # Parse language and character from the tag
            raw_tag_content = match.group(1)
            explicit_language, raw_character = self.parse_language_character_tag(raw_tag_content)
            
            # Update current character for text after this tag
            # IMPORTANT: Resolve language using original alias name before character normalization
            current_language = self.resolve_character_language(raw_character, explicit_language)
            original_character = raw_character  # Store original before normalization
            
            # Detect language-only tags: if the original tag had empty character part and raw_character
            # was defaulted to narrator, skip alias resolution to preserve narrator voice priority
            is_language_only_tag = (raw_character == self.default_character and 
                                   ':' in raw_tag_content and 
                                   raw_tag_content.split(':', 1)[1].strip() == '')
            
            current_character = self.normalize_character_name(raw_character, skip_narrator_alias=is_language_only_tag)
            current_pos = match.end()
        
        # Add remaining text after last tag (or entire line if no tags)
        remaining_text = line[current_pos:].strip()
        if remaining_text:
            segments.append(CharacterSegment(
                character=current_character,
                text=remaining_text,
                start_pos=line_start_pos + current_pos,
                end_pos=line_start_pos + len(line),
                language=current_language,
                original_character=original_character
            ))
        elif not segments and line.strip():
            # Line with only tags and no text after - still need a segment for the line
            # This handles edge cases like a line that is just "[character]"
            segments.append(CharacterSegment(
                character=current_character,
                text="",
                start_pos=line_start_pos,
                end_pos=line_start_pos + len(line),
                language=current_language,
                original_character=original_character if 'original_character' in locals() else current_character
            ))
        
        return segments
    
    def parse_character_mapping(self, text: str) -> Dict[str, List[str]]:
        """
        Parse text and return character-to-text mapping.
        
        Args:
            text: Input text with [Character] tags
            
        Returns:
            Dictionary mapping character names to list of text segments
        """
        segments = self.parse_text_segments(text)
        character_mapping = {}
        
        for segment in segments:
            if segment.character not in character_mapping:
                character_mapping[segment.character] = []
            character_mapping[segment.character].append(segment.text)
        
        return character_mapping
    
    def get_character_list(self, text: str) -> List[str]:
        """
        Get list of unique characters used in text.
        
        Args:
            text: Input text with [Character] tags
            
        Returns:
            List of unique character names used
        """
        segments = self.parse_text_segments(text)
        return list(set(segment.character for segment in segments))
    
    def remove_character_tags(self, text: str) -> str:
        """
        Remove all character tags from text, leaving only the speech content.
        
        Args:
            text: Input text with [Character] tags
            
        Returns:
            Text with character tags removed
        """
        return self.CHARACTER_TAG_PATTERN.sub('', text).strip()
    
    def split_by_character(self, text: str, include_language: bool = False) -> Union[List[Tuple[str, str]], List[Tuple[str, str, str]]]:
        """
        Split text by character, returning (character, text, language) tuples.
        This is the main method used by TTS nodes.
        
        Args:
            text: Input text with [Character] tags
            include_language: If True, returns (character, text, language) tuples
            
        Returns:
            List of (character_name, text_content, language) tuples if include_language=True
            List of (character_name, text_content) tuples if include_language=False (backward compatibility)
        """
        # print(f"🔍 Character Parser DEBUG: Input text: {repr(text)}")
        segments = self.parse_text_segments(text)
        
        if include_language:
            result = [(segment.character, segment.text, segment.language) for segment in segments]
        else:
            # Backward compatibility: return old tuple format
            result = [(segment.character, segment.text) for segment in segments]
        
        # print(f"🔍 Character Parser DEBUG: Parsed segments: {result}")
        return result
    
    def split_by_character_with_language(self, text: str) -> List[Tuple[str, str, str]]:
        """
        Split text by character, returning (character, text, language) tuples.
        Convenience method that always includes language information.
        
        Args:
            text: Input text with [Character] or [language:Character] tags
            
        Returns:
            List of (character_name, text_content, language_code) tuples
        """
        segments = self.parse_text_segments(text)
        return [(segment.character, segment.text, segment.language) for segment in segments]
    
    def validate_character_tags(self, text: str) -> Tuple[bool, List[str]]:
        """
        Validate character tags in text and return any issues.
        
        Args:
            text: Input text with [Character] tags
            
        Returns:
            Tuple of (is_valid, list_of_issues)
        """
        issues = []
        
        # Find all character tags
        tags = self.CHARACTER_TAG_PATTERN.findall(text)
        
        # Check for empty tags
        empty_tags = [tag for tag in tags if not tag.strip()]
        if empty_tags:
            issues.append(f"Found {len(empty_tags)} empty character tag(s)")
        
        # Check for unmatched brackets
        open_brackets = text.count('[')
        close_brackets = text.count(']')
        if open_brackets != close_brackets:
            issues.append(f"Unmatched brackets: {open_brackets} '[' vs {close_brackets} ']'")
        
        # Check for characters not in available list (if set)
        if self.available_characters:
            unknown_chars = []
            for tag in tags:
                normalized = self.normalize_character_name(tag)
                if normalized == self.default_character and tag.strip().lower() != self.default_character:
                    unknown_chars.append(tag)
            
            if unknown_chars:
                issues.append(f"Unknown characters (will use fallback): {', '.join(unknown_chars)}")
        
        return len(issues) == 0, issues
    
    def get_statistics(self, text: str) -> Dict[str, any]:
        """
        Get statistics about character usage in text.
        
        Args:
            text: Input text with [Character] tags
            
        Returns:
            Dictionary with statistics
        """
        segments = self.parse_text_segments(text)
        
        character_counts = {}
        character_lengths = {}
        
        for segment in segments:
            char = segment.character
            character_counts[char] = character_counts.get(char, 0) + 1
            character_lengths[char] = character_lengths.get(char, 0) + len(segment.text)
        
        total_chars = sum(character_counts.values())
        total_length = sum(character_lengths.values())
        
        return {
            "total_segments": len(segments),
            "unique_characters": len(character_counts),
            "character_counts": character_counts,
            "character_lengths": character_lengths,
            "total_character_switches": total_chars - 1,
            "total_text_length": total_length,
            "average_segment_length": total_length / len(segments) if segments else 0
        }


# Global instance for use across nodes
character_parser = CharacterParser()


def parse_character_text(text: str, available_characters: Optional[List[str]] = None) -> List[Tuple[str, str]]:
    """
    Convenience function to parse character text.
    
    Args:
        text: Input text with [Character] tags
        available_characters: Optional list of available character voices
        
    Returns:
        List of (character_name, text_content) tuples
    """
    if available_characters:
        character_parser.set_available_characters(available_characters)
    else:
        # Auto-discover characters if not provided
        try:
            from utils.voice.discovery import get_available_characters
            auto_chars = get_available_characters()
            if auto_chars:
                character_parser.set_available_characters(list(auto_chars))
        except Exception as e:
            print(f"⚠️ Character Parser: Auto-discovery failed: {e}")
    
    return character_parser.split_by_character(text)


def validate_character_text(text: str) -> Tuple[bool, List[str]]:
    """
    Convenience function to validate character text.
    
    Args:
        text: Input text with [Character] tags
        
    Returns:
        Tuple of (is_valid, list_of_issues)
    """
    return character_parser.validate_character_tags(text)