from app.core.llm import call_claude


class BaseAgent:
    def __init__(self, name: str, system_prompt: str):
        self.name = name
        self.system_prompt = system_prompt

    def run(self, prompt: str, max_tokens: int = 8000) -> str:
        return call_claude(self.system_prompt, prompt, max_tokens)
