# ⚠️ Транзит S0 (дормант). ListService мигрировал в app/contexts/lists/ (Срез 2a).
# Food/LLM-сервисы мигрировали в app/contexts/nutrition/services/ (Срез 2c, волна A)
# и удалены отсюда — re-export'ы сняты, иначе висят ссылки на удалённые файлы.
# Остаток (User/Notification) приводится к specs в S3 (модули ещё ссылаются на
# переехавшие kernel-символы → импортировать пакет рантаймом нельзя, это ожидаемо).
from .user_service import UserService
from .notification_service import NotificationService

__all__ = ["UserService", "NotificationService"]