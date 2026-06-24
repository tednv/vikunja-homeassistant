from datetime import datetime, date, timezone
from typing import cast, Optional

import homeassistant.util.dt as dt
from homeassistant.components.todo import TodoItem, TodoItemStatus, TodoListEntity, TodoListEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from pyvikunja.api import VikunjaAPI
from pyvikunja.models.project import Project
from pyvikunja.models.task import Task

from custom_components.vikunja import VikunjaDataUpdateCoordinator, DOMAIN, LOGGER
from custom_components.vikunja.const import DATA_PROJECTS_KEY, DATA_TASKS_KEY


async def async_setup_entry(
        hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    vikunja_data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not vikunja_data:
        LOGGER.error("No Vikunja data found in hass.data")
        return

    vikunja_api: VikunjaAPI = vikunja_data["api"]
    coordinator = vikunja_data["coordinator"]

    projects: list[Project] = [
        proj for proj in coordinator.data[DATA_PROJECTS_KEY].values()
        if proj.id != -1
    ]

    async_add_entities(
        (
            VikunjaTaskTodoListEntity(
                coordinator,
                vikunja_api.web_ui_link,
                project.id,
            )
            for project in projects
        ),
        True,
    )


def _convert_api_item(item: Task) -> TodoItem:
    status = TodoItemStatus.COMPLETED if item.done else TodoItemStatus.NEEDS_ACTION

    return TodoItem(
        summary=item.title,
        uid=str(item.id),
        status=status,
        due=item.due_date,
        description=item.description,
    )


class VikunjaTaskTodoListEntity(
    CoordinatorEntity, TodoListEntity
):
    _attr_has_entity_name = True

    def __init__(
            self,
            coordinator: VikunjaDataUpdateCoordinator,
            base_url: str,
            project_id,
    ) -> None:
        super().__init__(coordinator)
        self._base_url = base_url
        self._coordinator = coordinator
        self._project_id = project_id

        self._attr_supported_features = (
            TodoListEntityFeature.CREATE_TODO_ITEM
            | TodoListEntityFeature.UPDATE_TODO_ITEM
            | TodoListEntityFeature.DELETE_TODO_ITEM
            | TodoListEntityFeature.SET_DUE_DATE_ON_ITEM
            | TodoListEntityFeature.SET_DUE_DATETIME_ON_ITEM
            | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
        )

    @property
    def project(self) -> Project:
        return self._coordinator.data[DATA_PROJECTS_KEY][self._project_id]

    @property
    def name(self) -> str:
        return self.project.title

    @property
    def unique_id(self) -> str | None:
        return f"todo_list_{self.project.id}"

    def tasks_for_project(self) -> list[Task]:
        tasks = [
            task for task in self._coordinator.data[DATA_TASKS_KEY].values()
            if task.project_id == self._project_id
        ]

        # NEW: enforce newest-first ordering (stable + deterministic)
        return sorted(tasks, key=lambda t: t.id, reverse=True)

    def task_by_id(self, id: int) -> Optional[Task]:
        tasks = self.tasks_for_project()
        return next((task for task in tasks if task.id == id), None)

    @property
    def todo_items(self) -> list[TodoItem] | None:
        if self._coordinator.data is None:
            return None

        return [_convert_api_item(item) for item in self.tasks_for_project()]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        data = {
            "done": item.status == TodoItemStatus.COMPLETED,
            "title": item.summary,
            "description": item.description,
        }

        if item.due is not None and item.status != TodoItemStatus.COMPLETED:
            due = item.due

            if isinstance(due, date) and not isinstance(due, datetime):
                due = datetime(due.year, due.month, due.day, tzinfo=dt.UTC)

            due = dt.as_utc(due)

            data["due_date"] = (
                due.replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )

        await self.project.create_task(data)
        await self._coordinator.async_request_refresh()

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        for uid in uids:
            id = int(uid)
            task = self.task_by_id(id)

            if task is not None:
                await task.delete_task()

        await self._coordinator.async_request_refresh()

    async def async_update_todo_item(self, item: TodoItem) -> None:
        uid = int(item.uid)
        task = self.task_by_id(uid)

        new_data = {
            "done": item.status == TodoItemStatus.COMPLETED,
            "title": item.summary,
            "description": item.description,
        }

        from datetime import datetime, date
        from homeassistant.util import dt as dt_util

        if item.status != TodoItemStatus.COMPLETED and item.due is not None:
            due = item.due

            if isinstance(due, date) and not isinstance(due, datetime):
                due = datetime(due.year, due.month, due.day, tzinfo=dt_util.UTC)

            due = dt_util.as_utc(due)

            new_data["due_date"] = (
                due.replace(microsecond=0)
                .isoformat()
                .replace("+00:00", "Z")
            )
        else:
            new_data["due_date"] = None

        if task is not None:
            await task.update(new_data)

        await self._coordinator.async_request_refresh()
