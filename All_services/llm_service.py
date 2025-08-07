class LLMService:
    def __init__(self, api_key: str):
        import openai
        self.client = openai.OpenAI(api_key=api_key)

    def generate_text(self, prompt: str, model: str = "gpt-4", tone: str = "professional") -> str:
        response = self.client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": f"{tone} tone: {prompt}"}]
        )
        return response.choices[0].message.content.strip()