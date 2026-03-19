import os

from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer = HTTPBearer(auto_error=False)


async def require_api_key(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> str:
    api_key = os.getenv("DASHBOARD_API_KEY", "")
    if not credentials or not api_key or credentials.credentials != api_key:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")
    return credentials.credentials
