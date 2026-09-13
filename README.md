# AI Language Translation Tool

A professional Streamlit translator that uses free services through `deep-translator`. Google Translator is the primary service, with MyMemory as a fallback for manually selected source languages. It requires no API key and no billing account.

## Features

- Translate text between 16 languages, with automatic source-language detection.
- Keep a translation, its audio, and request status available during the browser session.
- Retry temporary translation rate limits with short exponential backoff.
- Cache up to 20 unique translations per browser session to avoid duplicate requests.
- Copy translations with `pyperclip`, with a safe manual-copy fallback when clipboard access is unavailable.
- Generate and play translated-text audio with gTTS.
- Show safe request-status details in an expandable panel.

## Project structure

```text
app.py                           # Streamlit UI, translation, copy, and TTS logic
requirements.txt                 # Python dependencies
.streamlit/secrets.toml.example  # Reserved for future secret-based services
.gitignore                       # Excludes local credentials and Python artifacts
```

## Installation

Use Python 3.10 or newer.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

## Run the app

```powershell
streamlit run app.py
```

## How to use it

1. Select a source language (or **Auto Detect**) and a target language.
2. Enter up to 5,000 characters of text.
3. Select **Translate text**.
4. Use **Copy text** to place the result on the local clipboard, or **Text to speech** to generate an MP3 player.

## Rate limits and troubleshooting

- The free services need an internet connection and may throttle heavy usage. The app automatically retries Google rate limits twice, then uses MyMemory as a backup when an explicit source language is selected. Automatic detection still requires Google to be available.
- If a translation fails, confirm that the selected languages are supported and keep text under 5,000 characters.
- Clipboard access can be blocked by remote desktops, containers, or operating-system settings. In that case, select the translated text and copy it manually.
- gTTS also needs internet access. If audio cannot be generated, retry later or choose a different target language.

## Secrets

This keyless version does not read a secrets file. `.streamlit/secrets.toml.example` is retained only as a safe template if a future integration needs credentials; `.streamlit/secrets.toml` is ignored by Git.
