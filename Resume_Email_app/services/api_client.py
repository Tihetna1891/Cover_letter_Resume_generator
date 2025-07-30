import httpx
import os
from dotenv import load_dotenv
from typing import Optional, Dict, Any
from fastapi import HTTPException, status
import google.generativeai as genai

import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # You can set it to INFO, WARNING, etc.
# Configure logging to output to console
handler = logging.StreamHandler()
handler.setLevel(logging.DEBUG)
load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

class AIService:
    @staticmethod
    def enhance_resume_text(raw_text: str, job_description: str = "") -> str:
        model = genai.GenerativeModel('gemini-pro')
        prompt = f"""
        Enhance this resume for better clarity and impact:
        {raw_text}
        
        {f"Tailor it for this job: {job_description}" if job_description else ""}
        Keep the original structure but improve wording.
        """
        response = model.generate_content(prompt)
        return response.text
class APIClient:
    def __init__(self):
        self.profile_api = os.getenv("PROFILE_API", "https://sandbox.appleazy.com/api/v1/user")
        self.job_api = os.getenv("JOB_API", "https://server.appleazy.com/api/v1/job-listing")
        self.timeout = 30.0

    async def get_user_profile(self, user_id: str):
        """Smart endpoint discovery with fallbacks"""
        endpoints = [
            f"{self.profile_api}/profiles/{user_id}",          # New format
            f"{self.profile_api}/profile/{user_id}",           # Common alternative
            f"{self.profile_api}/users/{user_id}",             # Another common pattern
            f"{self.profile_api}/get-profile/{user_id}"        # Legacy format
        ]

        async with httpx.AsyncClient() as client:
            for endpoint in endpoints:
                try:
                    response = await client.get(endpoint, timeout=self.timeout)
                    if response.status_code == 200:
                        data = response.json()
                        if data.get('resume') or data.get('cv'):
                            return self._normalize_profile(data)
                    
                except httpx.RequestError:
                    continue

            # Final fallback to mock data
            return self._get_mock_profile(user_id)

    def _normalize_profile(self, data: dict) -> dict:
        """Standardize different API response formats"""
        return {
            "name": data.get("name") or data.get("fullName"),
            "email": data.get("email"),
            "experience": data.get("experience") or data.get("workHistory") or [],
            "education": data.get("education") or data.get("academicHistory") or [],
            "skills": data.get("skills") or data.get("technicalSkills") or [],
            "resume": {
                "content": data.get("resume", {}).get("content") or 
                          data.get("cv", {}).get("content") or
                          data.get("attachments", {}).get("resumeContent")
            }
        }

    def _get_mock_profile(self, user_id: str) -> dict:
        """Fallback mock data"""
        return {
            "name": "Test User",
            "email": f"user_{user_id[:8]}@example.com",
            "experience": [{
                "title": "Software Developer",
                "company": "Sample Company",
                "duration": "2020-Present"
            }],
            "education": [{
                "degree": "B.Sc Computer Science",
                "institution": "State University"
            }],
            "skills": ["Python", "FastAPI", "Celery"],
            "resume": {
                "content": "Base64EncodedPlaceholder"
            }
        }

    async def get_job_listing(self, job_id: str) -> Dict[str, Any]:
        """Fetch job listing details"""
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