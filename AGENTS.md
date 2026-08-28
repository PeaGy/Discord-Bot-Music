# AGENTS.md

This file applies to the entire repository. It is operational guidance for coding
agents; `README.md` remains the user-facing product documentation.

## Project intent

Peto (repository name: Tracen Jukebox) is a Vietnamese-first Discord bot built on
`discord.py`. It combines music/voice playback, media downloads, Grok chat and
memory, image tools, Limbus Company knowledge, game notifications, and community
utilities. Preserve the existing conversational tone and Discord UX when making
changes. User-facing copy should normally be natural Vietnamese.

The production deployment is an Ubuntu VPS at `/home/ubuntu/peto`, managed by the
`peto.service` systemd unit. The same repository is also run on Windows for local
testing. Code must remain portable across both environments.

## Start here

Before editing:

1. Read the relevant module, its tests, `.env.example`, and the related README
   section.
2. Run `git status --short`. The worktree may contain user changes; do not discard,
   overwrite, or reformat unrelated work.
3. Search for an existing shared implementation before adding another one.
4. Make the smallest coherent change and update tests/documentation with it.

Primary commands:

```bash
python -m unittest discover -s tests
python bot.py
```

Run focused tests while iterating, for example:

```bash
python -m unittest tests.test_help tests.test_welcome
python -m unittest tests.test_limbus_gacha tests.test_limbus_assets
python -m unittest tests.test_ytdlp_support tests.test_long_audio_source
```

Some tests intentionally log warnings while exercising fallback paths. The test
process must still end in `OK`. Network probes belong in `scripts/manual/` and are
not part of the normal unit suite.

## Repository map

- `bot.py`: entry point, Discord intents, automatic extension loading, presence,
  and bot-wide voice-state cleanup.
- `commands/`: slash commands and command groups.
- `features/`: event listeners, background jobs, AI, downloads, game systems, and
  rich Discord views.
- `music/`: per-guild state, player pipeline, controls, and Spotify integration.
- `cache_manager.py`: audio caching, normalization, and temporary long-track audio.
- `ytdlp_support.py`: the single compatibility layer for yt-dlp proxy, JS runtime,
  PO-token provider, cookies, client selection, and transient retries.
- `user_memory.py` / `bot_memory.db`: personal AI history and memory.
- `study_mode.py`: study detection, formatting, and Discord study controls.
- `features/limbus_wiki.py`: wiki sync, FTS index, official-news cache, and artwork
  metadata.
- `features/limbus_kit_view.py`: Limbus embed renderer/helper; it has a no-op
  `setup()` because of the automatic extension loader.
- `features/limbus_gacha.py`: Standard Extraction simulation and collage rendering.
- `features/welcome.py` / `assets/welcome_teto.gif`: member welcome system.
- `guild_settings.py` / `guild_settings.db`: per-guild notification channels,
  roles, enablement, and legacy `.env` fallback handling.
- `tests/`: offline-first unit and regression tests.
- `scripts/manual/`: opt-in diagnostics that may contact external services.

## Extension loading rule

`MusicBot.setup_hook()` loads every non-underscore `.py` file in both `commands/`
and `features/` as a Discord extension. Therefore every such file must expose:

```python
async def setup(bot: commands.Bot) -> None:
    ...
```

If a file is only a helper, either place it outside these two directories, prefix
the filename with `_`, or provide an intentional no-op `setup()`. Do not rely on
filesystem/load order: a feature must initialize its own schema and tolerate an
older database until other extensions finish loading.

Slash commands are globally synchronized in `setup_hook()`. Avoid renaming public
commands without an explicit product decision.

## Async and Discord rules

- Never block the event loop with yt-dlp, Pillow, synchronous SQLite work, large
  filesystem operations, or CPU-heavy parsing. Use `asyncio.to_thread()` or the
  existing async wrappers.
- Acknowledge interactions promptly. Use `defer()` followed by an edit/follow-up
  for downloads, AI calls, sync jobs, and other work that may exceed three seconds.
- Discord message content is limited to 2,000 characters. Embed descriptions are
  limited to 4,096 characters and all embeds in one message share a 6,000-character
  aggregate limit. Reuse the existing pagination/file fallbacks; do not silently
  truncate important answers.
- `commands/help.py` paginates long categories. Keep new help content within that
  mechanism.
- Pass explicit `AllowedMentions` when the bot intentionally pings a member/role.
  Never let external text create `@everyone` or arbitrary role pings.
- Catch expected Discord failures (`NotFound`, `Forbidden`, `HTTPException`) at
  network boundaries and log enough IDs to diagnose them without exposing tokens.
- Components V2 (`LayoutView`) and classic `View` are not interchangeable. Match
  the API expected by the target message/follow-up.

## Environment and secrets

Configuration is loaded from `.env` through `config.py`. When adding a setting:

1. Use a clear prefixed name.
2. Add a commented example and explanation to `.env.example`.
3. Pick a safe disabled/fallback default.
4. Update the concise README configuration list when the setting is user-facing.

Never print, commit, replace, or upload:

- `.env` or Discord/API tokens;
- `.xai_tokens.json` and lock files;
- `cookies.txt` or browser/Pixiv cookies;
- production SQLite databases;
- private download files or user attachments.

Do not inspect secret values unless the task strictly requires a specific key's
presence. Redact secrets from logs and responses. `.env` is ignored by Git and is
not transferred by `git pull`; production changes must be applied separately on
the VPS.

Important generated paths are also ignored: `*.db`, `*.db-wal`, `*.db-shm`,
`audio_cache/`, `limbus_gacha_art_cache/`, and `temp_downloads/`. The legacy
workspace path `.cache` may be a file, so new features must not assume `.cache/`
can always be created.

## Persistent data and migrations

These files contain real user or operational state:

- `bot_memory.db`: AI chat and personal memory;
- `music_library.db`: favorites, playlists, listening history;
- `limbus_knowledge.db`: wiki pages/FTS, official notices, artwork metadata;
- `youtube_notifications.db`: Project Moon notification cursor;
- `daily_reset_notifications.db`: schedules already sent and DM subscriptions;
- `coupon_codes.db`: codes, voluntary UID/nickname profiles, redeem history;
- `guild_settings.db`: per-guild public notification destinations and roles;
- `audio_cache/`: normalized audio files.

Never delete or recreate them as a routine fix. Prefer additive migrations such as
`CREATE TABLE IF NOT EXISTS` and `ALTER TABLE` guarded by schema inspection. New
code must degrade gracefully when a production database predates a new table. Use
transactions, close connections, and preserve WAL compatibility.

Memory semantics are intentional: durable personal memory follows Discord
`user_id` across servers/DMs, while channel/reply context identifies who said what.
Anonymous mode must not read or write durable memory. Do not merge one user's
memory into another user's context.

## Music and yt-dlp invariants

- Route YouTube/yt-dlp configuration through `ytdlp_support.py`. Do not scatter
  hard-coded proxy, cookies, player-client, JS runtime, or PO-token options across
  commands.
- Home-PC behavior must remain unchanged when no `YTDLP_*` variables are set.
- The VPS may use WARP via `socks5://127.0.0.1:40000`, Node/EJS, and a bgutil HTTP
  provider at loopback port `4416`. Provider plugin/server versions must match.
- Treat 403, 429, bot checks, host-unreachable, and proxy resets as transient only
  where the shared retry layer already does so. Do not add unbounded retries.
- Cached short tracks are normalized for consistent playback. Proxied long YouTube
  tracks may use a local temporary bridge to avoid expiring Googlevideo URLs.
- Radio is a separate direct-stream path. Do not send radio through YouTube cache,
  normalization, or long-track download logic unless the user explicitly requests
  a radio change.
- Keep blocking yt-dlp extraction/download work off the event loop. Clean temporary
  files on success, failure, cancellation, and expired interactions.

`yt-dlp`, `yt-dlp-ejs`, and `bgutil-ytdlp-pot-provider` are compatibility-sensitive.
Do not bump one casually without checking the others and adding/updating a focused
test.

## AI behavior invariants

- Image generation/editing and Danbooru lookup must require explicit image intent.
  Ordinary requests to rewrite, translate, explain, or continue a conversation
  must not trigger image tools.
- Keep adaptive reasoning routing: low for ordinary chat/roleplay/memory/music,
  medium for Limbus/web/technical work, and high for Study Mode or genuinely
  multi-step reasoning.
- Preserve timeouts and bounded retries. A slow upstream request must not block
  unrelated users or wait indefinitely.
- Study answers use Discord-readable Markdown. Discord does not render LaTeX, so
  convert formulas to readable Unicode/plain-text code blocks where appropriate.
- Do not dump raw tool JSON, wiki rows, or search payloads into chat. Grok should
  synthesize an answer and distinguish verified facts from uncertainty.
- Owner-only blacklist and destructive memory commands must remain owner/admin
  restricted at both the UI and handler levels.

## Limbus and game systems

- Limbus answers should prefer the synchronized wiki database, then official
  Project Moon X/Steam sources for current announcements. Do not present search
  snippets as confirmed game facts.
- Keep Identity/E.G.O aliases normalized without replacing canonical titles.
- Kit embeds, skill-only embeds, Coin/status emoji placement, rarity colors, and
  Sin Affinity colors have dedicated regression tests. Update tests whenever their
  render rules change.
- Gacha reads `Extraction/Extraction List` from `limbus_knowledge.db`; it is a
  simulator only and must not consume currency or invent persistent inventory.
- Gacha must work before `wiki_assets` exists by falling back to MediaWiki artwork
  URLs. The tenth Standard Extraction pull guarantees 2-star or better.
- Daily Reset, coupon, and Project Moon notifications must be idempotent across
  restarts. Never ping roles in preview/warning messages unless explicitly designed.

## Welcome system

`WELCOME_ENABLED=true` also enables the privileged Discord members intent in
`bot.py`; production requires **Server Members Intent** in Discord Developer Portal.
The configured welcome channel needs View Channel, Send Messages, Embed Links, and
Attach Files. `WELCOME_RULES_CHANNEL_ID` and `WELCOME_ROLES_CHANNEL_ID` create safe,
clickable channel mentions. The bundled GIF is used by default so a signed Discord
CDN URL cannot expire. `/welcome preview` is owner-only.

## Tests and completion criteria

For a code change:

1. Add or update a regression test for the observed failure.
2. Run the narrowest relevant test module.
3. Run `python -m unittest discover -s tests` for shared/core changes when the
   environment has all dependencies.
4. Run `git diff --check`.
5. Report tests that could not run and why; never claim unrun tests passed.

Tests should be deterministic and offline unless placed under `scripts/manual/`.
Mock Discord/network boundaries and test pure parsers/renderers directly. Do not
depend on the user's production databases unless a test is explicitly guarded with
`skipUnless` and has a safe fallback.

## Deployment handoff

Do not assume local `.env`, cookies, databases, or caches are on the VPS. A normal
deployment after a pushed commit is:

```bash
cd /home/ubuntu/peto
git status --short
git pull --ff-only origin main
source .venv/bin/activate
python -m unittest <relevant-test-modules>
sudo systemctl restart peto
sudo systemctl status peto --no-pager -l
sudo journalctl -u peto -n 100 --no-pager -a -l
```

If `git status --short` shows tracked VPS changes, stop and inspect them instead of
forcing the pull. Do not use `git reset --hard` or delete production data.

Related production services may include `cloudflared`, `warp-svc`, and
`bgutil-pot`. Do not restart or edit them unless the task concerns download routing
or YouTube extraction. Only one Peto process should use the production Discord token
at a time; stop the VPS bot before running the same token on a home PC.

Commit and push only when the user requests or approves it. Keep commits focused;
never include ignored secrets, databases, caches, downloads, or unrelated user
changes.
