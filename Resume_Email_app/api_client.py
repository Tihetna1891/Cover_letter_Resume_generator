import httpx
import os
from dotenv import load_dotenv
from typing import Optional, Dict, Any
from fastapi import HTTPException, status
import google.generativeai as genai
import base64

import logging
logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)  # You can set it to INFO, WARNING, etc.
# Configure logging to output to console
handler = logging.StreamHandler()
handler.setLevel(logging.DEBUG)
load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
print(f"Gemini API Key: {'Exists' if os.getenv('GEMINI_API_KEY') else 'Missing'}")
class AIService:
    @staticmethod
    def enhance_resume_text(raw_text: str, job_description: str = "") -> str:
        if not os.getenv("GEMINI_API_KEY"):
            logger.warning("Gemini API key not configured")
            return raw_text
            
        try:
            genai.configure(
                api_key=os.getenv("GEMINI_API_KEY"),
                transport='rest'
            )
            
            # Try the latest stable models first
            model_priority = [
                'models/gemini-1.5-pro-latest',
                'models/gemini-1.5-flash-latest',
                'models/gemini-pro',
                'models/gemini-1.0-pro'
            ]
            
            for model_name in model_priority:
                try:
                    model = genai.GenerativeModel(model_name)
                    prompt = f"""Improve this resume for job application:
                    {raw_text}
                    
                    Job Description: {job_description}
                    Keep the original structure but enhance the wording."""
                    response = model.generate_content(prompt)
                    return response.text
                except Exception as e:
                    logger.debug(f"Model {model_name} failed: {str(e)}")
                    continue
            
            logger.warning("No compatible Gemini model found")
            return raw_text
            
        except Exception as e:
            logger.error(f"Gemini service error: {str(e)}")
            return raw_text
    # Add to AIService class
    @staticmethod
    def get_available_models():
        return [m.name for m in genai.list_models() if 'gemini' in m.name]

# Test connection during startup
print("Available Gemini models:", AIService.get_available_models())
class APIClient:
    def __init__(self):
        self.profile_api = os.getenv("PROFILE_API", "https://sandbox.appleazy.com/api/v1/user")
        self.job_api = os.getenv("JOB_API", "https://server.appleazy.com/api/v1/job-listing")
        self.timeout = 30.0

    async def get_user_profile(self, user_id: str):
        headers = {
        "Accept": "application/json",
        "User-Agent": "ResumeGenerator/1.0"
        }
    
        """Smart endpoint discovery with fallbacks"""
        endpoints = [
            # f"{self.profile_api}/{user_id}",
            # f"{self.profile_api}/profiles/{user_id}",          # New format
            # f"{self.profile_api}/profile/{user_id}",           # Common alternative
            # f"{self.profile_api}/users/{user_id}",             # Another common pattern
            f"{self.profile_api}/get-profile/{user_id}"        # Legacy format
        ]
        last_error = None
        async with httpx.AsyncClient() as client:
            for endpoint in endpoints:
                try:
                    response = await client.get(endpoint, headers=headers, timeout=self.timeout)
                    if response.status_code == 200:
                        data = response.json()
                        if data.get('resume') or data.get('cv'):
                            logger.info(f"Successfully fetched profile from {endpoint}")
                            return self._normalize_profile(data)
                    
                except Exception as e:
                    last_error = str(e)
                    logger.warning(f"Attempt failed for {endpoint}: {last_error}")
                    continue

            logger.warning("All profile API attempts failed, using mock data")
            return self._get_mock_profile(user_id)

        # async with httpx.AsyncClient() as client:
        #     for endpoint in endpoints:
        #         try:
        #             response = await client.get(endpoint, timeout=self.timeout)
        #             if response.status_code == 200:
        #                 data = response.json()
        #                 if data.get('resume') or data.get('cv'):
        #                     return self._normalize_profile(data)
                    
        #         except httpx.RequestError:
        #             continue

        #     # Final fallback to mock data
        #     return self._get_mock_profile(user_id)

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
        """Fallback mock data with valid base64 placeholder"""
        # Simple plain text encoded as base64
        mock_resume_content = base64.b64encode(b"Mock Resume Content\nName: Test User\nSkills: Python, FastAPI").decode()
        
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
                "content": mock_resume_content  # Valid base64
            }
        }
    # def _get_mock_profile(self, user_id: str) -> dict:
    #     """Fallback mock data"""
    #     return {
    #         "name": "Test User",
    #         "email": f"user_{user_id[:8]}@example.com",
    #         "experience": [{
    #             "title": "Software Developer",
    #             "company": "Sample Company",
    #             "duration": "2020-Present"
    #         }],
    #         "education": [{
    #             "degree": "B.Sc Computer Science",
    #             "institution": "State University"
    #         }],
    #         "skills": ["Python", "FastAPI", "Celery"],
    #         "resume": {
    #             "content": "Base64EncodedPlaceholder"
    #         }
    #     }

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