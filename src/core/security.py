import jwt
import requests
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jwt.exceptions import InvalidTokenError, PyJWTError
from typing import List, Dict, Any, Optional

from src.core.config import settings

# Security scheme using Bearer token
security_scheme = HTTPBearer()

# Simple in-memory cache for Keycloak public keys (JWKS)
_jwks_cache: Optional[Dict[str, Any]] = None
_jwks_last_fetched: float = 0.0
JWKS_CACHE_TTL = 3600.0  # 1 hour

def fetch_jwks() -> Dict[str, Any]:
    global _jwks_cache, _jwks_last_fetched
    import time
    
    current_time = time.time()
    if _jwks_cache and (current_time - _jwks_last_fetched < JWKS_CACHE_TTL):
        return _jwks_cache
        
    jwks_url = f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}/protocol/openid-connect/certs"
    try:
        r = requests.get(jwks_url, timeout=5.0)
        r.raise_for_status()
        _jwks_cache = r.json()
        _jwks_last_fetched = current_time
        return _jwks_cache
    except Exception as e:
        print(f"Error fetching JWKS from Keycloak: {e}")
        # Return empty JWKS so verification fails gracefully in production
        return {"keys": []}

def get_current_token_payload(credentials: HTTPAuthorizationCredentials = Depends(security_scheme)) -> Dict[str, Any]:
    token = credentials.credentials
    
    if settings.OIDC_MOCK_MODE:
        # In Mock Mode, we support both mock JWTs signed with 'mock-secret' (HS256)
        # and raw JSON-like parsing if signature check is bypassed.
        try:
            # Try to decode with mock secret
            payload = jwt.decode(token, "mock-secret", algorithms=["HS256"])
            return payload
        except PyJWTError:
            # Fallback: if signature fails, try decoding without signature verification
            # (only in mock development mode!)
            try:
                payload = jwt.decode(token, options={"verify_signature": False})
                return payload
            except PyJWTError as e:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid token format in Mock Mode: {str(e)}",
                    headers={"WWW-Authenticate": "Bearer"},
                )
                
    # Production Verification (OIDC / RS256 with JWKS)
    try:
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")
        if not kid:
            raise InvalidTokenError("Token is missing kid header claim.")
            
        jwks = fetch_jwks()
        public_key = None
        for key in jwks.get("keys", []):
            if key.get("kid") == kid:
                public_key = jwt.algorithms.RSAAlgorithm.from_jwk(key)
                break
                
        if not public_key:
            raise InvalidTokenError("Matching public key (kid) not found in JWKS.")
            
        # Verify and decode
        payload = jwt.decode(
            token,
            public_key,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.KEYCLOAK_CLIENT_ID,
            options={"verify_aud": True}
        )
        return payload
    except PyJWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token verification failed: {str(e)}",
            headers={"WWW-Authenticate": "Bearer"},
        )

class RoleChecker:
    def __init__(self, allowed_roles: List[str]):
        self.allowed_roles = allowed_roles

    def __call__(self, payload: Dict[str, Any] = Depends(get_current_token_payload)) -> Dict[str, Any]:
        roles = []
        
        # Extract Realm Roles
        realm_access = payload.get("realm_access", {})
        roles.extend(realm_access.get("roles", []))
        
        # Extract Client Roles
        resource_access = payload.get("resource_access", {})
        client_access = resource_access.get(settings.KEYCLOAK_CLIENT_ID, {})
        roles.extend(client_access.get("roles", []))
        
        # Extract direct roles claim (for simple tokens / mock payload)
        roles.extend(payload.get("roles", []))
        
        # Handle case-insensitive comparisons
        user_roles = [r.lower() for r in roles]
        allowed = [r.lower() for r in self.allowed_roles]
        
        # Check permission (Admins always bypass)
        if "admin" in user_roles:
            return payload
            
        has_role = any(role in allowed for role in user_roles)
        if not has_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: User does not have any of required roles {self.allowed_roles}. Found roles: {roles}"
            )
            
        return payload
