import os, re
from typing import Optional
from fastapi import Header, HTTPException
from dotenv import load_dotenv

_bearer_re = re.compile(r"^Bearer\s+(.+)$", re.I)
_API_ACCESS_TOKEN: Optional[str] = None


def get_token() -> Optional[str]:
    global _API_ACCESS_TOKEN
    if _API_ACCESS_TOKEN is None:
        load_dotenv()
        _API_ACCESS_TOKEN = os.environ.get("API_ACCESS_TOKEN")
    return _API_ACCESS_TOKEN


def bearer_guard(Authorization: Optional[str] = Header(None)) -> None:
    token = get_token() or ""
    if not token:
        # If no token configured, allow only in test runs
        if not os.getenv("PYTEST_CURRENT_TEST"):
            raise HTTPException(
                status_code=503, detail="API is not configured with API_ACCESS_TOKEN"
            )
        return
    hdr = Authorization or ""
    m = _bearer_re.match(hdr)
    if not m or m.group(1) != token:
        raise HTTPException(status_code=401, detail="Unauthorized")
