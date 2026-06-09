"""Синтетическая Shareable-модель — харнесс видимости (A5), только для тестов.

Отдельный модуль (не conftest): conftest pytest импортирует особым образом, и
объявление mapped-класса там приводит к двойной регистрации таблицы.
"""

from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.kernel.models import Shareable
from app.kernel.sharing import ShareableRepository


class Gift(Base, Shareable):
    __tablename__ = "test_gifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column()


class GiftRepository(ShareableRepository[Gift]):
    model = Gift
