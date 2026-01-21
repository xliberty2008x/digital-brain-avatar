import os
import jwt
from datetime import datetime, timedelta, timezone
from typing import Optional

# ТИМЧАСОВО: В реальних проєктах це має бути в .env
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-super-secret-key-change-me")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Створює підписаний JWT токен.
    
    Args:
        data: Дані, які ми хочемо покласти в токен (payload).
        expires_delta: Час дії токену.
        
    Returns:
        str: Закодований і підписаний токен.
    """
    
    # ТВОЄ ЗАВДАННЯ:
    # 1. Визнач час закінчення (expire). 
    #    Якщо expires_delta передано - використовуй його, 
    #    інакше - додай ACCESS_TOKEN_EXPIRE_MINUTES до поточного часу UTC.
    # 2. Додай цей час у словник to_encode під ключем "exp".
    # 3. Використай jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM) 
    #    щоб створити токен і поверни його.
    
    # Твій код тут...
    
    if expires_delta: 
        expires = datetime.now(timezone.utc) + expires_delta
    else:
        expires = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = data.copy()
    to_encode.update({"exp": expires})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_access_token(token: str) -> Optional[dict]:
    """
    Декодує та перевіряє токен.
    """
    try:
        # ТВОЄ ЗАВДАННЯ №2 (після першого):
        # Використай jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        # Якщо все ок - поверни розпакований словник.
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.ExpiredSignatureError:
        print("Токен прострочений")
        return None
    except jwt.PyJWTError:
        print("Помилка валідації токена")
        return None
