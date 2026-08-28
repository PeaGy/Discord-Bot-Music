import asyncio
import tempfile
import unittest
from pathlib import Path

from guild_ai_settings import (
    AIAdmissionController,
    AIAdmissionDenied,
    GLOBAL_MAX_CONCURRENT,
    GLOBAL_MAX_VIDEO_SECONDS,
    GuildAIPolicy,
    GuildAISettingsStore,
)
from commands.settings import AISettingsView


class GuildAISettingsStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = GuildAISettingsStore(Path(self.tempdir.name) / "settings.db")
        await self.store.init()

    async def asyncTearDown(self):
        self.tempdir.cleanup()

    async def test_policy_channels_roles_and_limits_are_persistent(self):
        policy = await self.store.ensure(10, legacy=True)
        self.assertEqual(policy.response_mode, "mention")
        self.assertTrue(policy.memory_enabled)
        self.assertTrue(policy.legacy)

        policy = await self.store.update(
            10,
            99,
            response_mode="channels",
            memory_enabled=False,
            max_concurrent=999,
            max_video_seconds=99999,
        )
        self.assertEqual(policy.response_mode, "channels")
        self.assertFalse(policy.memory_enabled)
        self.assertEqual(policy.max_concurrent, GLOBAL_MAX_CONCURRENT)
        self.assertEqual(policy.max_video_seconds, GLOBAL_MAX_VIDEO_SECONDS)

        await self.store.set_channels(10, [100, 101, 100])
        await self.store.set_roles(10, "study", [200, 201, 200])
        self.assertEqual(await self.store.list_channels(10), {100, 101})
        self.assertEqual(await self.store.list_roles(10, "study"), {200, 201})

    async def test_first_rollout_preserves_existing_and_later_guild_is_new(self):
        await self.store.seed_existing_guilds([1, 2])
        self.assertTrue((await self.store.get(1)).legacy)
        self.assertTrue((await self.store.get(2)).legacy)

        await self.store.seed_existing_guilds([1, 2, 3])
        self.assertFalse((await self.store.get(3)).legacy)

    async def test_ai_settings_panel_fits_discord_component_rows(self):
        cog = type("Cog", (), {"ai_store": self.store})()
        view = AISettingsView(
            cog,
            guild_id=1,
            user_id=2,
            role_capability="chat",
            policy=GuildAIPolicy(guild_id=1),
            channels=set(),
            roles=set(),
        )
        self.assertEqual(len(view.children), 7)
        embed = await view.build_embed()
        self.assertLessEqual(len(embed.description or ""), 4096)


class AIAdmissionControllerTests(unittest.IsolatedAsyncioTestCase):
    async def test_same_user_cannot_overlap(self):
        controller = AIAdmissionController()
        policy = GuildAIPolicy(guild_id=1, cooldown_seconds=0)
        async with controller.admit(1, 50, policy):
            with self.assertRaises(AIAdmissionDenied) as caught:
                async with controller.admit(1, 50, policy):
                    pass
        self.assertEqual(caught.exception.reason, "running")

    async def test_guild_limit_queues_fifo_without_blocking_other_guild(self):
        controller = AIAdmissionController()
        policy = GuildAIPolicy(guild_id=1, cooldown_seconds=0, max_concurrent=1)
        order = []
        first_started = asyncio.Event()
        release_first = asyncio.Event()

        async def first():
            async with controller.admit(1, 1, policy):
                order.append("first")
                first_started.set()
                await release_first.wait()

        async def second():
            await first_started.wait()
            async with controller.admit(1, 2, policy):
                order.append("second")

        first_task = asyncio.create_task(first())
        second_task = asyncio.create_task(second())
        await first_started.wait()
        await asyncio.sleep(0)
        async with controller.admit(2, 3, policy):
            order.append("other")
        release_first.set()
        await asyncio.gather(first_task, second_task)
        self.assertEqual(order, ["first", "other", "second"])

    async def test_cooldown_applies_after_an_accepted_request(self):
        controller = AIAdmissionController()
        policy = GuildAIPolicy(guild_id=1, cooldown_seconds=10)
        async with controller.admit(1, 7, policy):
            pass
        with self.assertRaises(AIAdmissionDenied) as caught:
            async with controller.admit(1, 7, policy):
                pass
        self.assertEqual(caught.exception.reason, "cooldown")
        self.assertGreater(caught.exception.retry_after, 0)

    async def test_heavy_cooldown_does_not_block_later_ordinary_chat(self):
        controller = AIAdmissionController()
        policy = GuildAIPolicy(
            guild_id=1,
            cooldown_seconds=0,
            heavy_cooldown_seconds=30,
        )
        async with controller.admit(1, 8, policy, heavy=True):
            pass
        async with controller.admit(1, 8, policy, heavy=False):
            pass
        with self.assertRaises(AIAdmissionDenied) as caught:
            async with controller.admit(1, 8, policy, heavy=True):
                pass
        self.assertEqual(caught.exception.reason, "heavy_cooldown")


if __name__ == "__main__":
    unittest.main()
