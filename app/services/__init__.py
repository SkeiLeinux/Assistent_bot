# ⚠️ Транзит S0 (дормант). ListService мигрировал в app/contexts/lists/ (Срез 2a) и удалён
# отсюда. Остаток (Food/User/Notification) приводится к specs в S2c/S3.
from .user_service import UserService
from .notification_service import NotificationService
from .food_service import FoodService
from .llm_service import LLMService

__all__ = [
    "UserService", "NotificationService",
    "FoodService", "LLMService",
]