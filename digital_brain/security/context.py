from contextvars import ContextVar
from typing import Optional

# Глобальна змінна для зберігання токена Агента в поточному потоці виконання
# Це дозволяє "прокидувати" токен в тулзи без явної передачі аргументів
agent_token_context: ContextVar[Optional[str]] = ContextVar("agent_token", default=None)

def set_agent_token(token: str):
    """Встановити токен для поточного контексту"""
    agent_token_context.set(token)

def get_agent_token() -> Optional[str]:
    """Отримати токен з поточного контексту"""
    return agent_token_context.get()
