"""Config flow for bemfa integration."""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import area_registry, device_registry, entity_registry
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .sync import Sync
from .const import (
    CONF_UID,
    DOMAIN,
    OPTIONS_CONFIG,
    OPTIONS_SELECT,
)
from .service import BemfaService

_LOGGER = logging.getLogger(__name__)

OPTIONS_SCOPE_ALL = "all"
OPTIONS_SCOPE_AREA_PREFIX = "area:"
OPTIONS_SCOPE_DEVICE_PREFIX = "device:"
OPTIONS_BACK = "__back__"

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_UID): str,
    }
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for bemfa."""

    VERSION = 1

    # Bemfa service uses uid to auth api calls. One shall provide his uid to config this integration.
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if user_input is None:
            return self.async_show_form(
                step_id="user", data_schema=STEP_USER_DATA_SCHEMA, last_step=True
            )

        # uid should match this regExp
        if not re.match("^[0-9a-f]{32}$", user_input[CONF_UID]):
            return self.async_show_form(
                step_id="user",
                data_schema=STEP_USER_DATA_SCHEMA,
                errors={"base": "invalid_uid"},
                last_step=True,
            )

        # Multiply integration instances with same uid may case unexpected results.
        # We treat the md5sum of each configured uid as unique.
        uid_md5 = hashlib.md5(user_input[CONF_UID].encode("utf-8")).hexdigest()
        await self.async_set_unique_id(uid_md5)
        self._abort_if_unique_id_configured()

        return self.async_create_entry(
            title="",
            data=user_input,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> OptionsFlowHandler:
        """Create the options flow."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle a option flow for bemfa."""

    # creat or modify a sync
    _is_create: bool

    # a dict to hold syncs when create / modify one of them
    # with this map we can get it in the next step
    _sync_dict: dict[str, Sync]

    # current sync we are creating or modifu
    _sync: Sync

    # current scope to filter selectable syncs
    _scope: str

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self._entry_id = config_entry.entry_id
        self._config = (
            config_entry.options[OPTIONS_CONFIG].copy()
            if OPTIONS_CONFIG in config_entry.options
            else {}
        )

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage the options."""
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "create_sync",
                "modify_sync",
                "destroy_sync",
            ],
        )

    async def async_step_create_sync(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Create a hass-to-bemfa sync."""
        if user_input is not None:
            self._scope = user_input[OPTIONS_SELECT]
            return await self.async_step_create_sync_entity()

        service = self._get_service()
        all_topics = await service.async_fetch_all_topics()
        all_syncs = service.collect_supported_syncs()
        self._sync_dict = {}
        for sync in all_syncs:
            if sync.topic not in all_topics:
                self._sync_dict[sync.entity_id] = sync

        if not bool(self._sync_dict):
            return self.async_show_form(step_id="empty", last_step=False)

        self._is_create = True

        return self._async_show_scope_form("create_sync")

    async def async_step_create_sync_entity(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select an entity to create a hass-to-bemfa sync."""
        if user_input is not None:
            if user_input[OPTIONS_SELECT] == OPTIONS_BACK:
                return self._async_show_scope_form("create_sync")
            self._sync = self._sync_dict[user_input[OPTIONS_SELECT]]
            return await self._async_step_sync_config()

        return self._async_show_sync_form("create_sync_entity")

    def _async_show_scope_form(self, step_id: str) -> FlowResult:
        options = self._generate_scope_options()
        if not bool(options):
            return self.async_show_form(step_id="empty", last_step=False)

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(OPTIONS_SELECT): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            last_step=False,
        )

    def _async_show_sync_form(self, step_id: str) -> FlowResult:
        syncs = self._filter_syncs_by_scope(self._scope)
        if not bool(syncs):
            return self.async_show_form(step_id="empty", last_step=False)

        entity_options = [
            SelectOptionDict(
                value=OPTIONS_BACK,
                label="↩ 返回上一步",
            )
        ]
        entity_options.extend(
            [
                SelectOptionDict(
                    value=sync.entity_id,
                    label=sync.generate_option_label(),
                )
                for sync in syncs
            ]
        )

        return self.async_show_form(
            step_id=step_id,
            data_schema=vol.Schema(
                {
                    vol.Required(OPTIONS_SELECT): SelectSelector(
                        SelectSelectorConfig(
                            options=entity_options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
            last_step=False,
        )

    async def async_step_modify_sync(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Modify a hass-to-bemfa sync."""
        if user_input is not None:
            self._scope = user_input[OPTIONS_SELECT]
            return await self.async_step_modify_sync_entity()

        service = self._get_service()
        all_topics = await service.async_fetch_all_topics()
        all_syncs = service.collect_supported_syncs()
        self._sync_dict = {}
        for sync in all_syncs:
            if sync.topic in all_topics:
                sync.name = all_topics[sync.topic]
                self._sync_dict[sync.entity_id] = sync

        if not bool(self._sync_dict):
            return self.async_show_form(step_id="empty", last_step=False)

        self._is_create = False

        return self._async_show_scope_form("modify_sync")

    async def async_step_modify_sync_entity(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Select a sync to modify."""
        if user_input is not None:
            if user_input[OPTIONS_SELECT] == OPTIONS_BACK:
                return self._async_show_scope_form("modify_sync")
            self._sync = self._sync_dict[user_input[OPTIONS_SELECT]]
            return await self._async_step_sync_config()

        return self._async_show_sync_form("modify_sync_entity")

    def _generate_scope_options(self) -> list[SelectOptionDict]:
        area_reg = area_registry.async_get(self.hass)
        device_reg = device_registry.async_get(self.hass)
        areas: dict[str, int] = {}
        devices: dict[str, int] = {}

        for sync in self._sync_dict.values():
            scope = self._get_sync_scope(sync)
            if scope["device_id"] is not None:
                devices[scope["device_id"]] = devices.get(scope["device_id"], 0) + 1
            if scope["area_id"] is not None:
                areas[scope["area_id"]] = areas.get(scope["area_id"], 0) + 1

        options = [
            SelectOptionDict(
                value=f"{OPTIONS_SCOPE_DEVICE_PREFIX}{device_id}",
                label="[设备] {name} ({count})".format(
                    name=self._get_device_name(device_reg.async_get(device_id)),
                    count=count,
                ),
            )
            for device_id, count in devices.items()
        ]
        options.sort(key=lambda option: option["label"])

        area_options = [
            SelectOptionDict(
                value=f"{OPTIONS_SCOPE_AREA_PREFIX}{area_id}",
                label="[区域] {name} ({count})".format(
                    name=area_reg.async_get_area(area_id).name,
                    count=count,
                ),
            )
            for area_id, count in areas.items()
            if area_reg.async_get_area(area_id) is not None
        ]
        area_options.sort(key=lambda option: option["label"])
        options.extend(area_options)

        options.append(
            SelectOptionDict(
                value=OPTIONS_SCOPE_ALL,
                label="[全部] 显示所有可选项 ({count})".format(
                    count=len(self._sync_dict),
                ),
            )
        )

        return options

    def _filter_syncs_by_scope(self, scope: str) -> list[Sync]:
        if scope == OPTIONS_SCOPE_ALL:
            return list(self._sync_dict.values())

        filtered_syncs: list[Sync] = []
        for sync in self._sync_dict.values():
            sync_scope = self._get_sync_scope(sync)
            if scope.startswith(OPTIONS_SCOPE_DEVICE_PREFIX):
                device_id = scope.removeprefix(OPTIONS_SCOPE_DEVICE_PREFIX)
                if sync_scope["device_id"] == device_id:
                    filtered_syncs.append(sync)
            elif scope.startswith(OPTIONS_SCOPE_AREA_PREFIX):
                area_id = scope.removeprefix(OPTIONS_SCOPE_AREA_PREFIX)
                if sync_scope["area_id"] == area_id:
                    filtered_syncs.append(sync)
        return filtered_syncs

    def _get_sync_scope(self, sync: Sync) -> dict[str, str | None]:
        if sync.entity_id.startswith("area."):
            return {
                "area_id": sync.entity_id.split(".", 1)[1],
                "device_id": None,
            }

        entity_reg = entity_registry.async_get(self.hass)
        device_reg = device_registry.async_get(self.hass)
        entity = entity_reg.async_get(sync.entity_id)
        if entity is None:
            return {
                "area_id": None,
                "device_id": None,
            }

        area_id = entity.area_id
        if area_id is None and entity.device_id is not None:
            device = device_reg.async_get(entity.device_id)
            if device is not None:
                area_id = device.area_id

        return {
            "area_id": area_id,
            "device_id": entity.device_id,
        }

    def _get_device_name(self, device) -> str:
        if device is None:
            return "未知设备"
        return device.name_by_user or device.name or device.model or device.id

    async def _async_step_sync_config(self) -> FlowResult:
        """Set details of a hass-to-bemfa sync."""
        if self._sync.topic in self._config:
            self._sync.config = self._config[self._sync.topic]

        return self.async_show_form(
            step_id=self._sync.get_config_step_id(),
            data_schema=vol.Schema(self._sync.generate_details_schema()),
        )

    async def async_step_sync_config_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set details of a hass-to-bemfa sensor sync."""
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_binary_sensor(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set details of a hass-to-bemfa binary sensor sync."""
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_climate(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set details of a hass-to-bemfa climate sync."""
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_cover(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set details of a hass-to-bemfa cover sync."""
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_fan(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set details of a hass-to-bemfa fan sync."""
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_light(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set details of a hass-to-bemfa light sync."""
        return await self._async_step_sync_config_done(user_input)

    async def async_step_sync_config_switch(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Set details of a hass-to-bemfa switch sync."""
        return await self._async_step_sync_config_done(user_input)

    async def _async_step_sync_config_done(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        service = self._get_service()
        if self._is_create:
            await service.async_create_sync(self._sync, user_input)
        else:
            await service.async_modify_sync(self._sync, user_input)

        # store config to integration options
        if self._sync.config:
            self._config[self._sync.topic] = self._sync.config
        elif self._sync.topic in self._config:
            self._config.pop(self._sync.topic)
        return self.async_create_entry(title="", data={OPTIONS_CONFIG: self._config})

    async def async_step_destroy_sync(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Destroy hass-to-bemfa sync(s)"""
        service = self._get_service()
        if user_input is not None:
            for topic in user_input[OPTIONS_SELECT]:
                await service.async_destroy_sync(topic)
                if topic in self._config:
                    self._config.pop(topic)
            return self.async_create_entry(
                title="", data={OPTIONS_CONFIG: self._config}
            )

        all_topics = await service.async_fetch_all_topics()
        all_syncs = service.collect_supported_syncs()
        topic_map: dict[str, str] = {}
        for sync in all_syncs:
            if sync.topic in all_topics:
                sync.name = all_topics[sync.topic]
                all_topics.pop(sync.topic)
                topic_map[sync.topic] = sync.generate_option_label()

        for (topic, name) in all_topics.items():
            topic_map[topic] = "[?] {name}".format(name=name)

        if not bool(topic_map):
            return self.async_show_form(step_id="empty", last_step=False)
        return self.async_show_form(
            step_id="destroy_sync",
            data_schema=vol.Schema(
                {
                    vol.Required(OPTIONS_SELECT): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=value,
                                    label=label,
                                )
                                for (value, label) in topic_map.items()
                            ],
                            mode=SelectSelectorMode.LIST,
                            multiple=True,
                        )
                    )
                }
            ),
        )

    async def async_step_empty(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """No syncs found."""
        return await self.async_step_init(user_input)

    def _get_service(self) -> BemfaService:
        return self.hass.data[DOMAIN].get(self._entry_id)["service"]
