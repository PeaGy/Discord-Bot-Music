"""Trạng thái phát nhạc độc lập cho từng Discord server."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GuildPlayerState:
    """Toàn bộ state thay đổi trong lúc phát nhạc của một guild."""

    queue: deque[dict] = field(default_factory=deque)
    history: deque[dict] = field(default_factory=lambda: deque(maxlen=20))
    autoplay: bool = False
    always_on: bool = False
    loop_mode: str = "off"
    play_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    idle_task: asyncio.Task | None = None
    text_channel: Any | None = None
    now_playing_message: Any | None = None

    def cancel_idle_task(self) -> None:
        task = self.idle_task
        self.idle_task = None
        if task and not task.done():
            task.cancel()

    def clear_playback(self, *, clear_history: bool = False) -> None:
        """Dọn hàng đợi và tác vụ tạm; giữ cài đặt autoplay/24-7."""
        self.queue.clear()
        if clear_history:
            self.history.clear()
        self.cancel_idle_task()
        self.now_playing_message = None


_guild_states: dict[int, GuildPlayerState] = {}


def get_guild_state(guild_or_id: Any) -> GuildPlayerState:
    """Lấy state theo Guild, Interaction/Guild có ``id`` hoặc guild ID."""
    guild_id = getattr(guild_or_id, "id", guild_or_id)
    if guild_id is None:
        raise ValueError("Không thể lấy music state khi thiếu guild ID.")

    guild_id = int(guild_id)
    state = _guild_states.get(guild_id)
    if state is None:
        state = GuildPlayerState()
        _guild_states[guild_id] = state
    return state


def remove_guild_state(guild_or_id: Any) -> GuildPlayerState | None:
    """Gỡ state khi bot rời guild; tác vụ idle còn chạy sẽ được hủy."""
    guild_id = getattr(guild_or_id, "id", guild_or_id)
    if guild_id is None:
        return None

    state = _guild_states.pop(int(guild_id), None)
    if state:
        state.cancel_idle_task()
    return state


def active_guild_states() -> dict[int, GuildPlayerState]:
    """Snapshot phục vụ chẩn đoán; không sửa trực tiếp dictionary trả về."""
    return dict(_guild_states)
