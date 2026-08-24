"""
Identity dependency for requests that have already passed through Traefik's
ForwardAuth middleware (see the auth-free Middleware + IngressRoute in
namespace `web`, pointing at auth-service's /verify).

TRUST BOUNDARY: this dependency reads X-User-Id / X-Username / X-User-Role
directly off the incoming request with NO independent verification -- no JWT
decode, no signature check, no DB lookup. This is only safe because
Traefik's authResponseHeaders config on the ForwardAuth middleware
guarantees these exact header names are always overwritten with the values
/verify returned; authResponseHeaders copies named headers FROM the auth
server's response onto the proxied request, it does not merge with or pass
through arbitrary incoming headers of the same name. A client-supplied
X-User-Id therefore cannot survive the proxy hop.

THIS DEPENDENCY MUST NEVER BE USED ON A ROUTE WHOSE INGRESSROUTE LACKS THE
FORWARDAUTH MIDDLEWARE. If that's ever missing, every request arrives with
no identity headers and gets a 401 below -- there is no fallback auth path,
by design.
"""
from fastapi import Header, HTTPException, status, Depends

ROLE_ORDER = {"free": 0, "power": 1, "super": 2}


class Identity:
    def __init__(self, user_id: str, username: str, role: str):
        self.user_id = user_id
        self.username = username
        self.role = role


async def get_identity(
    x_user_id: str | None = Header(default=None),
    x_username: str | None = Header(default=None),
    x_user_role: str | None = Header(default=None),
) -> Identity:
    if not x_user_id or not x_username or not x_user_role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing identity headers -- request did not pass through ForwardAuth",
        )
    return Identity(user_id=x_user_id, username=x_username, role=x_user_role)


def require_role(minimum: str):
    async def dependency(identity: Identity = Depends(get_identity)) -> Identity:
        if ROLE_ORDER.get(identity.role, -1) < ROLE_ORDER.get(minimum, 0):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return identity

    return dependency
