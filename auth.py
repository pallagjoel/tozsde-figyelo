import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel
from jose import JWTError, jwt
import bcrypt
import pyotp
import qrcode
import qrcode.image.svg
import base64
import io

from models import User, get_session
from sqlalchemy.orm import Session

# --- Security Configuration ---
SECRET_KEY = os.environ.get("SECRET_KEY", "super-secret-quant-key-for-development")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 1 week

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

router = APIRouter(prefix="/api/auth", tags=["auth"])

# --- Pydantic Models ---
class RegisterRequest(BaseModel):
    email: str
    password: str

class LoginRequest(BaseModel):
    email: str
    password: str

class VerifyMFARequest(BaseModel):
    email: str
    code: str

class Token(BaseModel):
    access_token: str
    token_type: str
    requires_mfa: bool = False

# --- Helper Functions ---
def verify_password(plain_password, hashed_password):
    return bcrypt.checkpw(plain_password.encode('utf-8'), hashed_password.encode('utf-8'))

def get_password_hash(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(minutes=15)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

# --- Dependencies ---
def yield_session():
    session = get_session()
    try:
        yield session
    finally:
        session.close()

def get_current_user(token: str = Depends(oauth2_scheme), session: Session = Depends(yield_session)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    user = session.query(User).filter(User.email == email).first()
    if user is None:
        raise credentials_exception
    return user

# --- Routes ---
@router.post("/register", response_model=Token)
def register(request: RegisterRequest, session: Session = Depends(yield_session)):
    user = session.query(User).filter(User.email == request.email).first()
    if user:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    hashed_pw = get_password_hash(request.password)
    # Generate an MFA secret automatically upon registration
    mfa_secret = pyotp.random_base32()
    
    new_user = User(
        email=request.email,
        password_hash=hashed_pw,
        mfa_secret=mfa_secret,
        mfa_enabled=False  # Must be enabled explicitly
    )
    session.add(new_user)
    session.commit()
    
    # Login the user automatically
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": new_user.email}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer", "requires_mfa": False}


@router.post("/login", response_model=Token)
def login(request: LoginRequest, session: Session = Depends(yield_session)):
    user = session.query(User).filter(User.email == request.email).first()
    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    if user.mfa_enabled:
        # If MFA is enabled, we return a flag telling the frontend to prompt for code
        # DO NOT issue a valid JWT yet
        return {"access_token": "", "token_type": "bearer", "requires_mfa": True}
        
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer", "requires_mfa": False}


@router.post("/verify-mfa", response_model=Token)
def verify_mfa(request: VerifyMFARequest, session: Session = Depends(yield_session)):
    user = session.query(User).filter(User.email == request.email).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
        
    totp = pyotp.TOTP(user.mfa_secret)
    if not totp.verify(request.code):
        raise HTTPException(status_code=401, detail="Invalid MFA code")
        
    # Valid code, issue JWT
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": user.email}, expires_delta=access_token_expires
    )
    return {"access_token": access_token, "token_type": "bearer", "requires_mfa": False}


@router.get("/mfa/setup")
def setup_mfa(current_user: User = Depends(get_current_user), session: Session = Depends(yield_session)):
    """Generate a QR code for Google Authenticator. Enables MFA automatically."""
    totp = pyotp.TOTP(current_user.mfa_secret)
    provisioning_uri = totp.provisioning_uri(name=current_user.email, issuer_name="Tőzsde Figyelő Quant")
    
    # Generate SVG QR Code
    factory = qrcode.image.svg.SvgImage
    qr = qrcode.make(provisioning_uri, image_factory=factory)
    stream = io.BytesIO()
    qr.save(stream)
    svg_data = stream.getvalue().decode('utf-8')
    
    # Enable MFA
    current_user.mfa_enabled = True
    session.commit()
    
    return {"qr_svg": svg_data, "secret": current_user.mfa_secret}

@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return current_user.to_dict()
