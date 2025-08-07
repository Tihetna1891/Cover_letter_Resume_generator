import httpx
import os
from dotenv import load_dotenv
from typing import Optional, Dict, Any
from fastapi import HTTPException, status
from llm_service import LLMService
import logging

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler()
handler.setLevel(logging.DEBUG)

load_dotenv()
llm_service = LLMService(os.getenv("OPENAI_API_KEY"))
print(f"OpenAI API Key: {'Exists' if os.getenv('OPENAI_API_KEY') else 'Missing'}")

class AIService:
    @staticmethod
    def enhance_resume_text(raw_text: str, job_description: str = "") -> str:
        if not os.getenv("OPENAI_API_KEY"):
            logger.warning("OpenAI API key not configured - skipping enhancement")
            return raw_text
        try:
            prompt = f"Improve this resume for job application:\n{raw_text}\n\nJob Description: {job_description}\nKeep the original structure but enhance the wording."
            return llm_service.generate_text(prompt, tone="professional")
        except Exception as e:
            logger.error(f"OpenAI service error: {str(e)}")
            return raw_text

    @staticmethod
    def get_available_models():
        return ["gpt-4", "gpt-3.5-turbo"]  # Simplified for OpenAI

print("Available OpenAI models:", AIService.get_available_models())

class APIClient:
    def __init__(self):
        self.profile_api = os.getenv("PROFILE_API", "https://sandbox.appleazy.com/api/v1/user")
        self.job_api = os.getenv("JOB_API", "https://server.appleazy.com/api/v1/job-listing")
        self.timeout = 30.0

    async def get_user_profile(self, user_id: str) -> dict:
        try:
            async with httpx.AsyncClient() as client:
                profile_url = f"{self.profile_api}/get-profile/{user_id}"
                response = await client.get(profile_url, timeout=self.timeout)
                response.raise_for_status()
                data = response.json()
                resume_url = data.get("data", {}).get("resume")
                if isinstance(resume_url, str):
                    resume_response = await client.get(resume_url, timeout=self.timeout)
                    resume_response.raise_for_status()
                    data["data"]["resume_content"] = base64.b64encode(resume_response.content).decode()
                return self._normalize_profile(data)
        except Exception as e:
            logger.error(f"Profile fetch failed: {str(e)}")
            return self._get_mock_profile(user_id)

    def _normalize_profile(self, data: dict) -> dict:
        resume_content = None
        if data.get("data", {}).get("resume"):
            if isinstance(data["data"]["resume"], str):
                resume_content = data["data"].get("resume_content")
            elif isinstance(data["data"]["resume"], dict):
                resume_content = data["data"]["resume"].get("content")
        return {
            "name": data.get("data", {}).get("username") or "Unknown",
            "email": data.get("data", {}).get("email") or "",
            "experience": self._parse_experience(data.get("data", {})),
            "education": [],
            "skills": self._parse_skills(data.get("data", {})),
            "resume": {"content": resume_content or ""}
        }

    def _parse_experience(self, data: dict) -> list:
        positions = data.get("position", "").split(",")
        return [{"title": pos.strip(), "company": ""} for pos in positions if pos.strip()]

    def _parse_skills(self, data: dict) -> list:
        skills = []
        if data.get("position"):
            skills.extend(skill.strip() for skill in data["position"].split(","))
        if data.get("preferredIndustry"):
            skills.append(data["preferredIndustry"])
        return list(set(skills))

    def _get_mock_profile(self, user_id: str) -> dict:
        return {
            "name": "Test User",
            "email": "test@example.com",
            "experience": [{"title": "Software Developer", "company": "Test Corp", "years": 3}],
            "skills": ["Python", "FastAPI", "Celery"],
            "resume": {"content": base64.b64encode(b"Mock PDF Content").decode()}
        }

    async def get_job_listing(self, job_id: str) -> Dict[str, Any]:
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(
                    f"{self.job_api}/{job_id}",
                    timeout=self.timeout
                )
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Job API error: {str(e)}"
                )

api_client = APIClient()