from ozon_tracker_bot.formatting import format_change
from ozon_tracker_bot.models import Order
from ozon_tracker_bot.poller import Poller
from ozon_tracker_bot.provider import TrackingSnapshot
from ozon_tracker_bot.service import CheckResult


class FakeRepository:
    def __init__(self, orders: list[Order]) -> None:
        self._orders = orders

    async def list_pollable_orders(self) -> list[Order]:
        return list(self._orders)


class FakeService:
    def __init__(self, repository: FakeRepository, results: dict[int, CheckResult]) -> None:
        self.repository = repository
        self._results = results

    async def check_pollable(self, order: Order) -> CheckResult:
        return self._results[order.id]


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str) -> None:
        self.sent.append((chat_id, text))


def _order(order_id: int, user_id: int) -> Order:
    return Order(id=order_id, user_id=user_id, tracking_number="ABC-123", title="Тест")


def _result(order: Order, previous_status: str | None, new_status: str) -> CheckResult:
    status_changed = previous_status is not None and previous_status != new_status
    return CheckResult(
        order=order,
        snapshot=TrackingSnapshot(order.tracking_number, new_status),
        status_changed=status_changed,
        previous_status=previous_status,
    )


async def test_poller_notifies_on_first_check_and_status_change() -> None:
    first_order = _order(1, 42)
    changed_order = _order(2, 42)
    unchanged_order = _order(3, 42)
    repository = FakeRepository([first_order, changed_order, unchanged_order])
    service = FakeService(
        repository,
        {
            1: _result(first_order, None, "В пути"),
            2: _result(changed_order, "В пути", "Заказ в пункте выдачи"),
            3: _result(unchanged_order, "В пути", "В пути"),
        },
    )
    bot = FakeBot()
    poller = Poller(service, bot, interval_seconds=900)  # type: ignore[arg-type]

    await poller.run_once()

    assert [(chat_id, text) for chat_id, text in bot.sent] == [
        (42, format_change(first_order, None)),
        (42, format_change(changed_order, "В пути")),
    ]


async def test_poller_swallows_check_failures() -> None:
    order = _order(1, 42)
    repository = FakeRepository([order])

    class FailingService:
        async def check_pollable(self, _: Order) -> CheckResult:
            raise RuntimeError("provider down")

    failing = FailingService()
    failing.repository = repository  # type: ignore[attr-defined]
    bot = FakeBot()
    poller = Poller(failing, bot, interval_seconds=900)  # type: ignore[arg-type]

    await poller.run_once()

    assert bot.sent == []
