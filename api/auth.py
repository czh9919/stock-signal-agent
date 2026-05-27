import os
from datetime import datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer

SECRET_KEY = os.environ.get("WEB_SECRET_KEY", "change-me-in-production-32chars!!")
ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def _web_user() -> str:
    return os.environ.get("WEB_USER", "admin")

def _web_pass_hash() -> str:
    return os.environ.get("WEB_PASS_HASH", "")

WEB_USER = property(_web_user)


def verify_password(plain: str) -> bool:
    h = _web_pass_hash()
    if h:
        return _pwd.verify(plain, h)
    return plain == os.environ.get("WEB_PASS", "")


def create_token(username: str) -> str:
    expire = datetime.utcnow() + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode({"sub": username, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme)) -> str:
    err = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Invalid or expired token",
                        headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if not username:
            raise err
        return username
    except JWTError:
        raise err
