"""
LOQI — Groq LLM Fallback (Cloud Brain)

Streaming chat completion with Groq's LPU inference.
Used ONLY when the local intent router has no match — open-ended questions,
drafting text, explaining concepts, etc.

Critical rule: LLM output is DATA, not AUTHORITY. Any action-shaped output
routes through confirm.py's gate, never executes directly.
"""

import re
from groq import Groq

import config
from tts import split_sentences


class Brain:
    """Groq-backed cloud reasoning with streaming + sentence chunking."""

    def __init__(
        self,
        api_key: str = config.GROQ_API_KEY,
        model: str = config.GROQ_MODEL,
        backup_model: str = config.GROQ_BACKUP_MODEL,
    ):
        if not api_key:
            print("  ⚠️  No GROQ_API_KEY set. Cloud fallback will fail.")
            print("     Get a free key at https://console.groq.com/keys")
            print("     Add it to your .env file: GROQ_API_KEY=your_key_here")

        self.client = Groq(api_key=api_key) if api_key else None
        self.model = model
        self.backup_model = backup_model

        # Conversation history (last N turns)
        self.history: list[dict] = []
        self.max_history = config.GROQ_HISTORY_LENGTH

    def ask(self, text: str, stream: bool = True):
        """
        Send a query to Groq and return the response.

        Args:
            text: User's transcribed utterance.
            stream: If True, returns a generator yielding sentences as they
                    become available. If False, returns the full response string.

        Returns:
            If stream=True: generator of sentence strings.
            If stream=False: full response string.
        """
        if not self.client:
            if stream:
                yield "I can't reach the cloud right now. My API key isn't set up yet."
                return
            else:
                return "I can't reach the cloud right now. My API key isn't set up yet."

        # Add user message to history
        self.history.append({"role": "user", "content": text})

        # Trim history to max length
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

        messages = [
            {"role": "system", "content": config.GROQ_SYSTEM_PROMPT},
            *self.history,
        ]

        if stream:
            yield from self._stream_response(messages)
        else:
            return self._full_response(messages)

    def _stream_response(self, messages: list[dict]):
        """
        Stream tokens from Groq, yield complete sentences as they form.

        This is the key latency optimization: TTS can start speaking sentence 1
        while sentence 2 is still being generated.
        """
        try:
            stream = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                max_tokens=config.GROQ_MAX_TOKENS,
                temperature=config.GROQ_TEMPERATURE,
            )
        except Exception as e:
            # Try backup model
            try:
                stream = self.client.chat.completions.create(
                    model=self.backup_model,
                    messages=messages,
                    stream=True,
                    max_tokens=config.GROQ_MAX_TOKENS,
                    temperature=config.GROQ_TEMPERATURE,
                )
            except Exception as e2:
                yield f"Sorry, I couldn't reach the cloud. Error: {e2}"
                return

        buffer = ""
        full_response = ""

        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                token = delta.content
                buffer += token
                full_response += token

                # Check if buffer contains a complete sentence
                # Split on sentence-ending punctuation followed by space or end
                sentences = re.split(r'(?<=[.!?])\s+', buffer)

                if len(sentences) > 1:
                    # Yield all complete sentences (everything except the last fragment)
                    for sentence in sentences[:-1]:
                        sentence = sentence.strip()
                        if sentence:
                            yield sentence

                    # Keep the incomplete last part in the buffer
                    buffer = sentences[-1]

        # Yield any remaining text in the buffer
        if buffer.strip():
            yield buffer.strip()

        # Save assistant response to history
        self.history.append({"role": "assistant", "content": full_response})

    def _full_response(self, messages: list[dict]) -> str:
        """Get a complete (non-streamed) response."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=False,
                max_tokens=config.GROQ_MAX_TOKENS,
                temperature=config.GROQ_TEMPERATURE,
            )
            content = response.choices[0].message.content or ""
        except Exception:
            try:
                response = self.client.chat.completions.create(
                    model=self.backup_model,
                    messages=messages,
                    stream=False,
                    max_tokens=config.GROQ_MAX_TOKENS,
                    temperature=config.GROQ_TEMPERATURE,
                )
                content = response.choices[0].message.content or ""
            except Exception as e:
                content = f"Sorry, I couldn't reach the cloud. Error: {e}"

        # Save to history
        self.history.append({"role": "assistant", "content": content})
        return content

    def clear_history(self):
        """Clear conversation history."""
        self.history.clear()

