import httpx
import asyncio
from pprint import pprint

API_ENDPOINTS = {
    "job_listing": "https://server.appleazy.com/api/v1/job-listing",
    "profiles": "https://sandbox.appleazy.com/api/v1/user/profiles",
    "single_profile": "https://sandbox.appleazy.com/api/v1/user/get-profile/{user_id}"
}

async def test_api(name, url, params=None, headers=None):
    print(f"\n=== Testing {name} API ===")
    print(f"URL: {url}")
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            
            print(f"Status: {resp.status_code}")
            print("Response Sample:")
            pprint(data if isinstance(data, dict) else data[:1], depth=2)
            
            return True
            
    except httpx.HTTPStatusError as e:
        print(f"HTTP Error: {e.response.status_code}")
        print(f"Response: {e.response.text[:200]}...")
    except Exception as e:
        print(f"Error: {type(e).__name__}: {str(e)}")
    
    return False

async def main():
    # Test Job Listing API
    job_ok = await test_api(
        "Job Listing",
        API_ENDPOINTS["job_listing"],
        params={"page": 1, "limit": 1}
    )
    
    # Test Profiles API
    profiles_ok = await test_api(
        "Profiles",
        API_ENDPOINTS["profiles"],
        params={"page": 1, "limit": 1}
    )
    
    # Test Single Profile API (if profiles exist)
    if profiles_ok:
        async with httpx.AsyncClient() as client:
            resp = await client.get(API_ENDPOINTS["profiles"], params={"page": 1, "limit": 1})
            if resp.status_code == 200:
                profiles = resp.json()
                if isinstance(profiles, list) and len(profiles) > 0:
                    user_id = profiles[0].get("userId") or profiles[0].get("id")
                    if user_id:
                        await test_api(
                            "Single Profile",
                            API_ENDPOINTS["single_profile"].format(user_id=user_id),
                            params={"field": "userId"}
                        )
                    else:
                        print("\nNo user ID found in profile data")
                else:
                    print("\nProfile API returned empty/invalid data")
    
    print("\n=== Summary ===")
    print(f"Job Listing API: {'OK' if job_ok else 'FAILED'}")
    print(f"Profiles API: {'OK' if profiles_ok else 'FAILED'}")

if __name__ == "__main__":
    asyncio.run(main())