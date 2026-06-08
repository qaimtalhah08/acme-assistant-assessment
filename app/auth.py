# ============================================================
# auth.py — Keycloak JWT Token Verification + RBAC
# ============================================================
# This module handles all authentication and authorisation
# for the Acme Assistant API.
#
# Responsibilities:
#   1. Fetch and cache Keycloak public keys (JWKS)
#   2. Verify incoming JWT Bearer tokens against those keys
#   3. Enforce role-based access control (RBAC) via
#      FastAPI dependency injection
#
# Authentication Flow:
#   Client → sends Bearer token in Authorization header
#   auth.py → fetches Keycloak public keys (cached)
#   auth.py → verifies token signature, issuer, audience
#   auth.py → extracts user roles from token payload
#   FastAPI → injects verified payload into route handler
#
# RBAC Roles (defined in Keycloak realm):
#   sales_user   — read-only access to customer and issue data
#   support_user — read access + can create next actions
#   admin        — full access including system logs
# ============================================================

from jose import jwt, JWTError
from fastapi import Header, HTTPException, Depends
import requests
import os


# ─── Keycloak Configuration ───────────────────────────────────
# KEYCLOAK_URL uses the internal Docker service name 'keycloak'
# so FastAPI can reach it within the Docker Compose network.
# EXPECTED_ISSUER uses 'localhost' because JWT tokens are issued
# to the browser, which accesses Keycloak via localhost:8080.
# These two URLs differ intentionally — this is a known
# trade-off documented in the README.
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://keycloak:8080")
REALM = os.getenv("REALM", "acme")
JWKS_URL = f"{KEYCLOAK_URL}/realms/{REALM}/protocol/openid-connect/certs"
EXPECTED_ISSUER = f"http://localhost:8080/realms/{REALM}"
EXPECTED_AUDIENCE = "account"

# In-memory JWKS cache — avoids repeated calls to Keycloak
# for every incoming request. Cache is cleared and refreshed
# automatically if a token's key ID (kid) is not found.
_jwks_cache = None


# ─── JWKS Fetch ───────────────────────────────────────────────
def get_jwks() -> dict:
    """
    Fetch JSON Web Key Set (JWKS) from Keycloak.

    JWKS contains the public RSA keys used to verify JWT
    token signatures. These keys are published by Keycloak
    at the /certs endpoint and rotate periodically.

    Caching strategy:
      Keys are cached in memory after the first successful
      fetch. If a token arrives with an unknown key ID (kid),
      the cache is cleared and keys are re-fetched, handling
      key rotation transparently without service restart.

    Returns:
        dict: JWKS response containing a list of public keys

    Raises:
        HTTPException 503: If Keycloak is unreachable
        HTTPException 500: If the fetch fails for any reason
    """
    global _jwks_cache

    # Return cached keys if available to avoid redundant calls
    if _jwks_cache:
        return _jwks_cache

    try:
        resp = requests.get(JWKS_URL, timeout=5)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        return _jwks_cache

    except requests.exceptions.ConnectionError:
        # Keycloak container is not reachable — likely not started
        raise HTTPException(
            status_code=503,
            detail="Keycloak not reachable"
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to fetch auth keys: {e}"
        )


# ─── Token Verification ───────────────────────────────────────
def verify_token(authorization: str = Header(None)) -> dict:
    """
    Verify an incoming JWT Bearer token and return its payload.

    This function is used as a FastAPI dependency. Any route
    that requires authentication includes this function in its
    Depends() declaration. FastAPI will call it automatically
    before the route handler executes.

    Verification steps:
      1. Extract the token from the Authorization header
      2. Decode the token header to get the key ID (kid)
      3. Find the matching RSA public key in the JWKS
      4. Verify the token signature using RS256 algorithm
      5. Validate the issuer and audience claims
      6. Return the decoded payload containing user info

    The payload contains:
      - sub: Keycloak user ID (unique identifier)
      - preferred_username: e.g. "salesuser"
      - email: user email address
      - realm_access.roles: list of assigned roles

    Args:
        authorization: Value of the HTTP Authorization header,
                       expected format: "Bearer <jwt_token>"

    Returns:
        dict: Decoded and verified JWT payload

    Raises:
        HTTPException 401: Missing, empty, or invalid token
        HTTPException 500: Unexpected server error during verification
    """
    # Reject requests with no Authorization header
    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Authorization header missing"
        )

    # Strip the "Bearer " prefix to get the raw token string
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Token is empty"
        )

    jwks = get_jwks()

    try:
        # Decode the token header without verifying the signature.
        # This allows us to extract the key ID (kid) which tells
        # us which Keycloak public key to use for verification.
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")

        if not kid:
            raise HTTPException(
                status_code=401,
                detail="Token missing kid"
            )

        # Find the public key whose kid matches the token header
        rsa_key = next(
            (k for k in jwks.get("keys", []) if k.get("kid") == kid),
            None
        )

        # If no matching key found, the cache may be stale.
        # Clear the cache, re-fetch keys, and try once more.
        # This handles Keycloak key rotation without restart.
        if not rsa_key:
            global _jwks_cache
            _jwks_cache = None
            jwks = get_jwks()
            rsa_key = next(
                (k for k in jwks.get("keys", []) if k.get("kid") == kid),
                None
            )

        if not rsa_key:
            raise HTTPException(
                status_code=401,
                detail="No matching public key found"
            )

        # Fully verify the token:
        #   - Signature: verified against the RSA public key
        #   - Algorithm: only RS256 is accepted
        #   - Audience:  must be "account" (Keycloak default)
        #   - Issuer:    must match our Keycloak realm URL
        #   - Expiry:    checked automatically by python-jose
        payload = jwt.decode(
            token,
            rsa_key,
            algorithms=["RS256"],
            audience=EXPECTED_AUDIENCE,
            issuer=EXPECTED_ISSUER
        )

        return payload

    except JWTError as e:
        # Covers expired tokens, invalid signatures, wrong issuer
        raise HTTPException(
            status_code=401,
            detail=f"Invalid token: {e}"
        )
    except HTTPException:
        # Re-raise HTTPExceptions without wrapping them
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Auth error: {e}"
        )


# ─── Role Extraction Helper ───────────────────────────────────
def get_user_roles(payload: dict) -> list:
    """
    Extract the list of realm roles from a decoded JWT payload.

    Keycloak stores roles in the realm_access.roles claim.
    These are the roles assigned to the user in the Keycloak
    realm configuration (keycloak/realm.json).

    Args:
        payload: Decoded JWT payload dict from verify_token()

    Returns:
        list: Role name strings, e.g. ["sales_user", "offline_access"]
    """
    return payload.get("realm_access", {}).get("roles", [])


# ─── Generic Role Requirement ─────────────────────────────────
def require_role(required_role: str):
    """
    Factory function that returns a FastAPI dependency checking
    for a specific role.

    This provides a flexible, reusable way to protect routes
    with any arbitrary role requirement. The returned function
    is used directly as a FastAPI Depends() argument.

    Usage example:
        @router.get("/admin-only")
        def admin_route(payload = Depends(require_role("admin"))):
            ...

    Args:
        required_role: The role name that must be present

    Returns:
        Callable: FastAPI dependency function that verifies
                  the role and returns the payload if valid
    """
    def role_checker(payload: dict = Depends(verify_token)):
        roles = get_user_roles(payload)
        if required_role not in roles:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Required role: {required_role}"
            )
        return payload
    return role_checker


# ─── Pre-built Role Dependencies ──────────────────────────────
# These are convenience dependencies used directly in routes.py.
# Each one calls verify_token() first, then checks the role.
# FastAPI's dependency injection handles the chaining automatically.


def require_sales_or_above(payload: dict = Depends(verify_token)):
    """
    Allow any authenticated user with a business role.

    Permitted roles: sales_user, support_user, admin
    Blocked: unauthenticated requests, system accounts

    Use for routes that expose read-only customer and issue data.
    This is the minimum access level in the system.
    """
    roles = get_user_roles(payload)
    if not {"sales_user", "support_user", "admin"}.intersection(roles):
        raise HTTPException(
            status_code=403,
            detail="Access denied. Login required."
        )
    return payload


def require_support_or_above(payload: dict = Depends(verify_token)):
    """
    Allow support staff and administrators only.

    Permitted roles: support_user, admin
    Blocked: sales_user, unauthenticated requests

    Use for routes that create or modify issue data,
    such as creating next actions for customer issues.
    """
    roles = get_user_roles(payload)
    if not {"support_user", "admin"}.intersection(roles):
        raise HTTPException(
            status_code=403,
            detail="Access denied. Support role required."
        )
    return payload


def require_admin(payload: dict = Depends(verify_token)):
    """
    Allow administrators only — the most restrictive gate.

    Permitted roles: admin
    Blocked: sales_user, support_user, unauthenticated requests

    Use for routes exposing sensitive system data such as
    request logs, audit trails, or user management endpoints.
    """
    roles = get_user_roles(payload)
    if "admin" not in roles:
        raise HTTPException(
            status_code=403,
            detail="Access denied. Admin role required."
        )
    return payload
 
 