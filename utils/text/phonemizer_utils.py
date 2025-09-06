"""
Universal Phonemizer Utilities for F5-TTS Multilingual Support

This module provides a unified interface for phonemization that works with either:
- phonemizer (Linux/Mac with system espeak)
- espeak-phonemizer-windows (Windows with bundled binaries)

Automatically detects which package is available and provides fallbacks.
"""

import re
from typing import List, Optional, Tuple

class UniversalPhonemizer:
    """
    Universal phonemizer that works with either phonemizer or espeak-phonemizer-windows.
    Provides automatic detection and fallback to character-based processing.
    """
    
    def __init__(self):
        self.backend = None
        self.phonemizer = None
        self._initialize_backend()
    
    def _initialize_backend(self):
        """Initialize the best available phonemization backend"""
        
        # Try espeak-phonemizer-windows first (Windows, bundled binaries)
        try:
            from espeak_phonemizer import Phonemizer
            self.phonemizer = Phonemizer()
            # Test it works
            self.phonemizer.phonemize("test", voice="en")
            self.backend = "espeak-phonemizer-windows"
            return
        except Exception:
            pass
        
        # Try standard phonemizer (Linux/Mac with system espeak)
        try:
            from phonemizer import phonemize
            # Test if espeak backend works
            phonemize("test", language="en", backend="espeak")
            self.backend = "phonemizer"
            return
        except Exception:
            pass
        
        # No phonemization available - will use fallback
        self.backend = "fallback"
    
    def is_available(self) -> bool:
        """Check if phonemization is available"""
        return self.backend in ["phonemizer", "espeak-phonemizer-windows"]
    
    def get_backend_info(self) -> str:
        """Get information about current backend"""
        if self.backend == "espeak-phonemizer-windows":
            return "espeak-phonemizer-windows (bundled Windows binaries)"
        elif self.backend == "phonemizer":
            return "phonemizer + system espeak"
        else:
            return "fallback (character-based processing)"
    
    def phonemize_text(self, text: str, language: str = "en") -> str:
        """
        Convert text to IPA phonemes using available backend.
        
        Args:
            text: Text to phonemize
            language: Language code (e.g. 'pl', 'de', 'fr', 'es')
            
        Returns:
            Phonemized text (IPA) or original text if phonemization fails
        """
        if not text.strip():
            return text
            
        try:
            if self.backend == "espeak-phonemizer-windows":
                return self._phonemize_with_espeak_windows(text, language)
            elif self.backend == "phonemizer":
                return self._phonemize_with_standard(text, language)
            else:
                return text  # Fallback: return original text
        except Exception as e:
            # If phonemization fails, return original text
            print(f"Warning: Phonemization failed for '{text[:50]}...': {e}")
            return text
    
    def _phonemize_with_espeak_windows(self, text: str, language: str) -> str:
        """Phonemize using espeak-phonemizer-windows"""
        # Map language codes to espeak voices
        voice_map = {
            'en': 'en',
            'pl': 'pl',
            'de': 'de', 
            'fr': 'fr',
            'es': 'es',
            'it': 'it',
            'pt': 'pt',
            'ru': 'ru',
            'zh': 'zh',
            'ja': 'ja',
            'ko': 'ko',
            'ar': 'ar',
            'hi': 'hi',
            'th': 'th'
        }
        
        voice = voice_map.get(language, 'en')  # Default to English
        
        ipa_text = self.phonemizer.phonemize(
            text, 
            voice=voice,
            keep_clause_breakers=True,
            word_separator=' ',
            no_stress=False
        )
        
        # Clean up IPA text
        return self._clean_ipa_text(ipa_text)
    
    def _phonemize_with_standard(self, text: str, language: str) -> str:
        """Phonemize using standard phonemizer package"""
        from phonemizer import phonemize
        
        # Map our language codes to phonemizer language codes
        language_map = {
            'en': 'en-us',
            'pl': 'pl',
            'de': 'de',
            'fr': 'fr',
            'es': 'es', 
            'it': 'it',
            'pt': 'pt',
            'ru': 'ru',
            'zh': 'zh',
            'ja': 'ja',
            'ko': 'ko',
            'ar': 'ar',
            'hi': 'hi',
            'th': 'th'
        }
        
        phonemizer_lang = language_map.get(language, 'en-us')
        
        ipa_text = phonemize(
            text,
            language=phonemizer_lang,
            backend='espeak',
            strip=False,
            preserve_punctuation=True,
            with_stress=True
        )
        
        # Clean up IPA text
        return self._clean_ipa_text(ipa_text)
    
    def _clean_ipa_text(self, ipa_text: str) -> str:
        """Clean up IPA text output"""
        # Remove language markings like (en), (pl), (de), etc.
        ipa_text = re.sub(r'\([a-z]{2,3}\)', '', ipa_text)
        
        # Remove extra whitespace
        ipa_text = ' '.join(ipa_text.split())
        
        return ipa_text


# Global phonemizer instance
_global_phonemizer = None

def get_phonemizer() -> UniversalPhonemizer:
    """Get global phonemizer instance (singleton)"""
    global _global_phonemizer
    if _global_phonemizer is None:
        _global_phonemizer = UniversalPhonemizer()
    return _global_phonemizer


def should_use_phonemization(model_name: str, text_list: List[str]) -> bool:
    """
    Determine if phonemization should be used for this model and text.
    
    Args:
        model_name: F5-TTS model name
        text_list: List of text strings to analyze
        
    Returns:
        True if phonemization should be used
    """
    # Check if phonemization is available
    phonemizer = get_phonemizer()
    if not phonemizer.is_available():
        return False
    
    # IMPORTANT: Models without vocab files (like F5-PT-BR) NEED phonemization
    # because they must convert their language text to work with English vocab
    model_lower = model_name.lower()
    models_requiring_phonemization = ['ptbr', 'pt-br', 'pt_br']  # Add more as needed
    
    if any(indicator in model_lower for indicator in models_requiring_phonemization):
        print(f"🦜 Using phonemization for {model_name} - converts Portuguese to work with English vocab")
        return True
    
    # Check if model path suggests non-English language
    non_english_indicators = [
        'polish', 'german', 'french', 'spanish', 'italian', 'portuguese',
        'russian', 'arabic', 'hindi', 'thai', 'japanese', 'korean',
        'pl', 'de', 'fr', 'es', 'it', 'pt', 'ru', 'ar', 'hi', 'th', 'ja', 'ko'
    ]
    
    if any(indicator in model_lower for indicator in non_english_indicators):
        return True
    
    # Check for special characters in text that suggest non-English
    special_chars = set('ąćęłńóśźżĄĆĘŁŃÓŚŹŻäöüßÄÖÜàâæçéèêëîïôùûÀÂÆÇÉÈÊËÎÏÔÙÛáéíñóúüÁÉÍÑÓÚÜ')
    
    for text in text_list:
        if any(char in special_chars for char in text):
            return True
    
    # Check for tokenizer.json (indicates IPA-based model)
    if hasattr(model_name, 'startswith') and model_name.startswith('local:'):
        # For local models, we could check for tokenizer.json file existence
        # This would require path resolution, skip for now
        pass
    
    return False


def detect_language_from_text(text: str) -> str:
    """
    Detect language from text content based on character patterns.
    Uses comprehensive character sets for 50+ languages.
    
    Args:
        text: Text to analyze
        
    Returns:
        Canonical language code (e.g. 'pl', 'de', 'fr')
    """
    # Extended language detection using character sets
    language_chars = {
        # European languages with special characters
        'pl': 'ąćęłńóśźżĄĆĘŁŃÓŚŹŻ',  # Polish
        'de': 'äöüßÄÖÜ',  # German
        'fr': 'àâæçéèêëîïôùûÀÂÆÇÉÈÊËÎÏÔÙÛ',  # French
        'es': 'áéíñóúüÁÉÍÑÓÚÜ',  # Spanish
        'it': 'àèéìíîòóùúÀÈÉÌÍÎÒÓÙÚ',  # Italian
        'pt': 'àáâãçéêíóôõúÀÁÂÃÇÉÊÍÓÔÕÚ',  # Portuguese
        'cs': 'áčďéěíňóřšťúůýžÁČĎÉĚÍŇÓŘŠŤÚŮÝŽ',  # Czech
        'sk': 'áäčďéíľĺňóôŕšťúýžÁÄČĎÉÍĽĹŇÓÔŔŠŤÚÝŽ',  # Slovak
        'hu': 'áéíóöőúüűÁÉÍÓÖŐÚÜŰ',  # Hungarian
        'ro': 'ăâîșțĂÂÎȘȚ',  # Romanian
        'hr': 'čćđšžČĆĐŠŽ',  # Croatian
        'sr': 'чћџшжЧЋЏШЖ',  # Serbian (Cyrillic)
        'bg': 'абвгдежзийклмнопрстуфхцчшщъьюяАБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЬЮЯ',  # Bulgarian
        'ru': 'абвгдежзийклмнопрстуфхцчшщъыьэюяАБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ',  # Russian
        'el': 'αβγδεζηθικλμνξοπρστυφχψωΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ',  # Greek
        'tr': 'çğıöşüÇĞIİÖŞÜ',  # Turkish
        'is': 'áðéíóúýþæöÁÐÉÍÓÚÝÞÆÖ',  # Icelandic
        'da': 'æøåÆØÅ',  # Danish
        'no': 'æøåÆØÅ',  # Norwegian
        'sv': 'äöåÄÖÅ',  # Swedish
        'fi': 'äöåÄÖÅ',  # Finnish
        'et': 'äöõüšžÄÖÕÜŠŽ',  # Estonian
        'lv': 'āčēģīķļņšūžĀČĒĢĪĶĻŅŠŪŽ',  # Latvian
        'lt': 'ąčęėįšųūžĄČĘĖĮŠŲŪŽ',  # Lithuanian
        'sl': 'čšžČŠŽ',  # Slovenian
        'nl': '',  # Dutch (uses mostly standard Latin)
        
        # Asian languages with distinct scripts
        'zh': '一丁七万三不与专且世丘中为主举了事二于五些什人',  # Chinese (sample chars)
        'ja': 'あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをんアイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン',  # Japanese
        'ko': '가나다라마바사아자차카타파하거너더러머버서어저처커터퍼허고노도로모보소오조초코토포호구누두루무부수우주추쿠투푸후',  # Korean
        'th': 'กขคงจซดตนบปผฟมยรลวศษสหอะาิีืึุู',  # Thai
        'vi': 'àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ',  # Vietnamese
        
        # Middle Eastern and South Asian
        'ar': 'ابتثجحخدذرزسشصضطظعغفقكلمنهوي',  # Arabic
        'fa': 'ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهی',  # Persian/Farsi
        'he': 'אבגדהוזחטיכלמנסעפצקרשת',  # Hebrew
        'ur': 'آابپتٹثجچحخدڈذرڑزژسشصضطظعغفقکگلمنوہی',  # Urdu
        'hi': 'अआइईउऊएऐओऔकखगघचछजझटठडढणतथदधनपफबभमयरलवशषसह',  # Hindi
        'bn': 'অআইঈউঊএঐওঔকখগঘচছজঝটঠডঢণতথদধনপফবভমযরলশষসহ',  # Bengali
        'ta': 'அஆஇஈউஊஎஏஐஒஓஔகஙசஞடணதநபமயரலவழளறன',  # Tamil
        'te': 'అఆఇఈఉఊఎఏఐఒఓఔకఖగఘచఛజఝటఠడఢణతథదధనపఫబభమయరలవశషసహ',  # Telugu
        'ml': 'അആഇഈഉഊഎഏഐഒഓഔകഖഗഘങചഛജഝഞടഠഡഢണതഥദധനപഫബഭമയരലവശഷസഹ',  # Malayalam
        'kn': 'ಅಆಇಈಉಊಎಏಐಒಓಔಕಖಗಘಙಚಛಜಝಞಟಠಡಢಣತಥದಧನಪಫಬಭಮಯರಲವಶಷಸಹ',  # Kannada
        'gu': 'અઆઇઈઉઊએઐઓઔકખગઘચછજઝટઠડઢણતથદધનપફબભમયરલવશષસહ',  # Gujarati
        
        # Southeast Asian and Others
        'id': '',  # Indonesian (uses standard Latin)
        'ms': '',  # Malay (uses standard Latin)
        'tl': '',  # Filipino/Tagalog (mostly standard Latin with some Spanish influence)
        'sw': '',  # Swahili (uses standard Latin)
        'af': '',  # Afrikaans (uses standard Latin)
    }
    
    # Check for languages with distinctive character sets
    for lang, chars in language_chars.items():
        if chars and any(char in text for char in chars):
            # Use character parser's alias resolution to get canonical form
            try:
                from utils.text.character_parser import CharacterParser
                parser = CharacterParser()
                return parser.resolve_language_alias(lang)
            except ImportError:
                return lang
    
    # Default to English for standard Latin text
    return 'en'


def convert_text_with_smart_phonemization(text_list: List[str], model_name: str = "") -> List[List[str]]:
    """
    Convert text using smart phonemization or fallback to character-based processing.
    
    This is the main entry point that F5-TTS should use instead of convert_char_to_pinyin.
    
    Args:
        text_list: List of text strings to process
        model_name: F5-TTS model name for context
        
    Returns:
        List of processed text (as character lists for model input)
    """
    # Import here to avoid circular dependencies
    try:
        from engines.f5_tts.model.utils import convert_char_to_pinyin
    except ImportError:
        # Fallback if F5-TTS not available
        def convert_char_to_pinyin(texts):
            return [list(text) for text in texts]
    
    # Check if we should use phonemization
    if should_use_phonemization(model_name, text_list):
        phonemizer = get_phonemizer()
        
        print(f"🦜 Using phonemization for F5-TTS: {phonemizer.get_backend_info()}")
        
        processed_texts = []
        for text in text_list:
            # Detect language from text
            detected_lang = detect_language_from_text(text)
            
            # Phonemize the text
            phonemized = phonemizer.phonemize_text(text, detected_lang)
            
            # Convert to character list for model input
            processed_texts.append(list(phonemized))
        
        return processed_texts
    else:
        # Use standard character-to-pinyin conversion
        return convert_char_to_pinyin(text_list)


# Convenience functions for direct use
def phonemize(text: str, language: str = "en") -> str:
    """Convenience function for direct phonemization"""
    phonemizer = get_phonemizer()
    return phonemizer.phonemize_text(text, language)


def is_phonemization_available() -> bool:
    """Check if phonemization is available"""
    phonemizer = get_phonemizer()
    return phonemizer.is_available()


def get_phonemization_info() -> str:
    """Get information about phonemization backend"""
    phonemizer = get_phonemizer()
    return phonemizer.get_backend_info()