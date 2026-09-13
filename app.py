"""A keyless Streamlit language translator using deep-translator."""

from __future__ import annotations

from io import BytesIO
import html
import time

import pyperclip
import streamlit as st
from deep_translator import GoogleTranslator, MyMemoryTranslator
from deep_translator.exceptions import (
    InvalidSourceOrTargetLanguage,
    NotValidLength,
    RequestError,
    TooManyRequests,
    TranslationNotFound,
)
from gtts import gTTS
from gtts.tts import gTTSError
from pyperclip import PyperclipException
from requests.exceptions import RequestException


MAX_TEXT_CHARACTERS = 5_000
MAX_TRANSLATION_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (1, 2)
SESSION_CACHE_LIMIT = 20

LANGUAGE_CODES = {
    "Auto Detect": "auto",
    "English": "en",
    "Urdu": "ur",
    "Arabic": "ar",
    "French": "fr",
    "German": "de",
    "Spanish": "es",
    "Italian": "it",
    "Portuguese": "pt",
    "Russian": "ru",
    "Chinese": "zh-CN",
    "Japanese": "ja",
    "Korean": "ko",
    "Hindi": "hi",
    "Turkish": "tr",
    "Dutch": "nl",
    "Bengali": "bn",
}
LANGUAGES = list(LANGUAGE_CODES)
TARGET_LANGUAGES = [language for language in LANGUAGES if language != "Auto Detect"]
MYMEMORY_LANGUAGE_NAMES = {
    "English": "english",
    "Urdu": "urdu",
    "Arabic": "arabic",
    "French": "french",
    "German": "german",
    "Spanish": "spanish",
    "Italian": "italian",
    "Portuguese": "portuguese",
    "Russian": "russian",
    "Chinese": "chinese simplified",
    "Japanese": "japanese",
    "Korean": "korean",
    "Hindi": "hindi",
    "Turkish": "turkish",
    "Dutch": "dutch",
    "Bengali": "bengali",
}


class TranslationServiceError(Exception):
    """An expected translation failure with a safe message for the UI."""

    def __init__(self, user_message: str, status: str) -> None:
        self.user_message = user_message
        self.status = status
        super().__init__(user_message)


def initialise_state() -> None:
    """Create all per-session values in one predictable place."""
    defaults = {
        "translated_text": "",
        "translation_response": None,
        "translation_cache": {},
        "audio_bytes": None,
    }
    for key, value in defaults.items():
        st.session_state.setdefault(key, value)


def validate_translation_request(text: str, source_language: str, target_language: str) -> str:
    """Validate server-side because widget constraints are not a security boundary."""
    cleaned_text = text.strip()
    if not cleaned_text:
        raise TranslationServiceError("Please enter some text to translate.", "empty_input")
    if len(cleaned_text) > MAX_TEXT_CHARACTERS:
        raise TranslationServiceError(
            f"Please limit text to {MAX_TEXT_CHARACTERS:,} characters per translation.",
            "text_too_long",
        )
    if source_language not in LANGUAGE_CODES or target_language not in LANGUAGE_CODES:
        raise TranslationServiceError("Please select supported languages.", "invalid_language")
    return cleaned_text


def _translate_with_google(text: str, source_language: str, target_language: str) -> str:
    """Use the primary translator and retry short-lived HTTP 429 rate limits."""
    source_code = LANGUAGE_CODES[source_language]
    target_code = LANGUAGE_CODES[target_language]

    for attempt in range(MAX_TRANSLATION_ATTEMPTS):
        try:
            translated = GoogleTranslator(source=source_code, target=target_code).translate(text)
            if not translated:
                raise TranslationServiceError("No translation was returned. Please try again.", "empty_response")
            return translated
        except TooManyRequests as error:
            if attempt == MAX_TRANSLATION_ATTEMPTS - 1:
                raise TranslationServiceError(
                    "The translation service is busy. Please wait a minute and try again.",
                    "rate_limited",
                ) from error
            time.sleep(RETRY_DELAYS_SECONDS[attempt])
        except (RequestError, RequestException) as error:
            raise TranslationServiceError(
                "Unable to connect to the translation service. Please check your connection and try again.",
                "network_error",
            ) from error
        except (InvalidSourceOrTargetLanguage, NotValidLength) as error:
            raise TranslationServiceError("Unable to translate this text with the selected languages.", "translation_error") from error
        except TranslationNotFound:
            raise
        except TranslationServiceError:
            raise
        except Exception as error:
            raise TranslationServiceError("Unable to translate the text. Please try again.", "service_error") from error

    raise TranslationServiceError("Unable to translate the text. Please try again.", "service_error")


def _translate_with_mymemory(text: str, source_language: str, target_language: str) -> str:
    """Use MyMemory as a backup when Google is unavailable or throttled."""
    try:
        translated = MyMemoryTranslator(
            source=MYMEMORY_LANGUAGE_NAMES[source_language],
            target=MYMEMORY_LANGUAGE_NAMES[target_language],
        ).translate(text)
        if not translated:
            raise TranslationServiceError("No translation was returned. Please try again.", "empty_response")
        return translated
    except TranslationServiceError:
        raise
    except (RequestError, RequestException) as error:
        raise TranslationServiceError(
            "Unable to connect to the backup translation service. Please try again.",
            "network_error",
        ) from error
    except Exception as error:
        raise TranslationServiceError("Unable to translate the text. Please try again.", "service_error") from error


def translate_text(text: str, source_language: str, target_language: str) -> str:
    """Translate with Google first, then use MyMemory when Google is unavailable."""
    try:
        return _translate_with_google(text, source_language, target_language)
    except TranslationNotFound as google_error:
        if source_language == "Auto Detect":
            raise TranslationServiceError(
                "Automatic language detection is temporarily unavailable. Please select the source language and try again.",
                "auto_detect_unavailable",
            ) from google_error
        try:
            return _translate_with_mymemory(text, source_language, target_language)
        except TranslationServiceError as fallback_error:
            raise TranslationServiceError("Unable to translate the text. Please try again.", "service_error") from fallback_error
    except TranslationServiceError as google_error:
        # MyMemory requires an explicit source language, but it can keep the
        # app working when Google temporarily returns HTTP 429 or is offline.
        fallback_statuses = {"rate_limited", "network_error", "service_error"}
        if source_language == "Auto Detect" or google_error.status not in fallback_statuses:
            raise
        try:
            return _translate_with_mymemory(text, source_language, target_language)
        except TranslationServiceError:
            # Preserve the original Google error so the UI gives an accurate
            # explanation (especially the useful rate-limit message).
            raise google_error


def get_translation(text: str, source_language: str, target_language: str) -> tuple[str, bool]:
    """Use a bounded, per-session cache to reduce duplicate translation requests."""
    cache_key = (text, source_language, target_language)
    cache: dict[tuple[str, str, str], str] = st.session_state.translation_cache
    if cache_key in cache:
        return cache[cache_key], True

    translated = translate_text(text, source_language, target_language)
    if len(cache) >= SESSION_CACHE_LIMIT:
        cache.pop(next(iter(cache)))
    cache[cache_key] = translated
    return translated, False


def copy_to_clipboard(text: str) -> None:
    """Copy translated text, raising a clear error when the host blocks clipboard access."""
    if not text:
        raise ValueError("There is no translated text to copy.")
    pyperclip.copy(text)


def text_to_speech(text: str, target_language: str) -> bytes:
    """Generate MP3 bytes for the selected target language."""
    if not text:
        raise ValueError("There is no translated text to speak.")
    audio_buffer = BytesIO()
    gTTS(text=text, lang=LANGUAGE_CODES[target_language]).write_to_fp(audio_buffer)
    return audio_buffer.getvalue()


def apply_styles() -> None:
    """Keep the single navy SaaS palette requested for the app."""
    st.markdown(
        """<style>
        :root { --navy:#1E3A5F; --soft-blue:#EEF4F9; --page:#F5F7FA; --ink:#1F2937; --muted:#64748B; --line:#D9E2EC; }
        .stApp { background:var(--page); color:var(--ink); } .block-container { max-width:1040px; padding-top:3.4rem; padding-bottom:3rem; }
        .hero { text-align:center; margin:0 auto 2.2rem; } .hero h1 { color:var(--navy); font-size:clamp(2rem,4vw,2.8rem); margin:0; letter-spacing:-.04em; }
        .hero p,.helper { color:var(--muted); } .hero p { font-size:1.06rem; margin:.55rem 0 0; } .eyebrow { color:var(--navy); font-size:.78rem; font-weight:700; letter-spacing:.12em; text-transform:uppercase; margin-bottom:.45rem; }
        .section-title { color:var(--navy); font-size:1.08rem; font-weight:700; margin:.1rem 0 1rem; }
        div[data-testid="stVerticalBlockBorderWrapper"] { background:#FFF; border:1px solid var(--line); border-radius:14px; box-shadow:0 5px 16px rgba(30,58,95,.05); }
        div[data-testid="stVerticalBlockBorderWrapper"] > div { padding:1.35rem; } label,.stSelectbox label,.stTextArea label { color:var(--ink)!important; font-weight:600!important; }
        .stTextArea textarea,div[data-baseweb="select"] > div { background:#FFF; border-color:var(--line); border-radius:8px; }
        .stButton > button,.stFormSubmitButton > button { width:100%; background:var(--navy); color:#FFF; border:1px solid var(--navy); border-radius:8px; font-weight:650; min-height:2.75rem; }
        .result-box { min-height:152px; box-sizing:border-box; border:1px solid var(--line); border-radius:8px; background:var(--soft-blue); padding:1.1rem 1.15rem; color:var(--muted); line-height:1.6; }
        .result-box.has-text { color:var(--ink); white-space:pre-wrap; } @media (max-width:700px) { .block-container { padding:1.5rem 1rem 2rem; } }
        </style>""",
        unsafe_allow_html=True,
    )


def main() -> None:
    st.set_page_config(page_title="AI Translator", page_icon="🌐", layout="wide")
    initialise_state()
    apply_styles()
    st.markdown("""<div class="hero"><div class="eyebrow">Language tools</div><h1>AI Translator</h1><p>Fast and simple text translation</p></div>""", unsafe_allow_html=True)

    with st.container(border=True):
        st.markdown('<div class="section-title">Translation service</div>', unsafe_allow_html=True)
        st.caption("Free Google Translator service via deep-translator. No API key or billing setup is required.")

    st.write("")
    with st.container(border=True):
        st.markdown('<div class="section-title">Translate text</div>', unsafe_allow_html=True)
        with st.form("translation_form", border=False):
            source_col, target_col = st.columns(2, gap="large")
            with source_col:
                source_language = st.selectbox("Source language", LANGUAGES, key="source_language")
            with target_col:
                target_language = st.selectbox("Target language", TARGET_LANGUAGES, key="target_language")
            source_text = st.text_area(
                "Text to translate",
                placeholder="Type or paste text here...",
                height=175,
                max_chars=MAX_TEXT_CHARACTERS,
                key="source_text",
            )
            st.caption(f"Maximum {MAX_TEXT_CHARACTERS:,} characters per translation.")
            _, submit_col, _ = st.columns([1, 1.25, 1])
            with submit_col:
                submitted = st.form_submit_button("Translate text", type="primary", icon=":material/translate:")

    if submitted:
        try:
            clean_text = validate_translation_request(source_text, source_language, target_language)
            with st.spinner("Translating your text..."):
                translated, cache_hit = get_translation(clean_text, source_language, target_language)
            st.session_state.translated_text = translated
            st.session_state.audio_bytes = None
            st.session_state.translation_response = {
                "status": "success",
                "service": "deep-translator / Google Translator",
                "source_language": source_language,
                "target_language": target_language,
                "cached": cache_hit,
            }
            st.success("Translation complete." if not cache_hit else "Translation loaded from this session's cache.")
        except TranslationServiceError as error:
            st.session_state.translation_response = {"status": error.status, "service": "deep-translator / Google Translator"}
            st.error(error.user_message)

    st.write("")
    with st.container(border=True):
        st.markdown('<div class="section-title">Translated text</div>', unsafe_allow_html=True)
        translated_text = st.session_state.translated_text
        if translated_text:
            st.markdown(f'<div class="result-box has-text">{html.escape(translated_text)}</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="result-box">Your translated result will appear here.</div>', unsafe_allow_html=True)

        copy_col, speech_col, _ = st.columns([1, 1.35, 2.2])
        with copy_col:
            copy_requested = st.button("Copy text", icon=":material/content_copy:", disabled=not translated_text)
        with speech_col:
            speech_requested = st.button("Text to speech", icon=":material/volume_up:", disabled=not translated_text)

        if copy_requested:
            try:
                copy_to_clipboard(translated_text)
                st.success("Translated text copied.")
            except (PyperclipException, ValueError):
                st.warning("Clipboard access is unavailable in this environment. Select and copy the text manually.")

        if speech_requested:
            try:
                with st.spinner("Preparing audio..."):
                    st.session_state.audio_bytes = text_to_speech(translated_text, target_language)
                st.success("Audio is ready.")
            except (gTTSError, ValueError):
                st.warning("Text-to-speech is unavailable for this translation. Please try again later.")

        if st.session_state.audio_bytes:
            st.audio(st.session_state.audio_bytes, format="audio/mp3")

    st.write("")
    with st.expander("Translation response", expanded=False):
        if st.session_state.translation_response:
            st.json(st.session_state.translation_response)
        else:
            st.caption("Translation status details will appear here after a request.")


if __name__ == "__main__":
    main()
