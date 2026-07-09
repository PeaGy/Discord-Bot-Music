This project might be fork from Ryuz-V but it got deleted.
CHECK HIM HERE 👉 https://github.com/Ryuz-V

# Discord Music Bot

A powerful, user-friendly, and feature-rich Discord Music Bot built with Python. This bot allows you to play high-quality music in your Discord voice channels with a wide variety of features including loop, autoplay, lyrics integration, and 24/7 continuous playback.

## 🌟 Features

### Core Functionality
- **High-Quality Audio**: Stream music with high bitrate and no interruptions.
- **Multiple Sources**: Play music from your favorite platforms seamlessly.
- **Queue Management**: Easily manage your playlist with skip, previous, pause, and stop commands.
- **Advanced Playback**:
  - **Autoplay**: Automatically queues related tracks when the playlist ends.
  - **Loop**: Repeat your favorite song or the entire queue.
  - **24/7 Mode**: Keep the bot in the voice channel permanently.

### User Experience
- **Lyrics Support**: Instantly fetch and display lyrics for the currently playing song.
- **Interactive UI**: Slash commands and button interactions for a modern Discord experience.
- **Radio Mode**: Continuous radio streaming for your server.

## 📜 Command List

Dưới đây là danh sách các lệnh có sẵn trong bot này.:

| Command | File | Description |
| :--- | :--- | :--- |
| `/247` | `247.py` | Toggles 24/7 mode (keeps the bot in the voice channel indefinitely). |
| `/autoplay` | `autoplay.py` | Toggles autoplay mode to automatically play related songs. |
| `/connect` | `connect.py` | Connects the bot to your current voice channel. |
| `/help` | `help.py` | Displays the help menu and a list of all available commands. |
| `/leave` | `leave.py` | Disconnects the bot from the voice channel. |
| `/loop` | `loop.py` | Toggles loop for the current track or the entire queue. |
| `/lyric` | `lyric.py` | Fetches and displays the lyrics for the current or specified song. |
| `/pause` | `pause.py` | Pauses the currently playing track. |
| `/play` | `play.py` | Plays a song from a given name or URL. |
| `/previous` | `previous.py` | Plays the previous song in the queue history. |
| `/radio` | `radio.py` | Starts a continuous radio stream. |
| `/resume` | `resume.py` | Resumes a paused track. |
| `/skip` | `skip.py` | Skips the current track and plays the next one in the queue. |
| `/stop` | `stop.py` | Stops the music completely and clears the queue. |
| `/search` | `search.py` | Search for music on YouTube. |
| `/queue` | `queue.py` | Display a list of songs in the queue. |

## 🛠️ Technologies Used

- **Python 3.8+**
- **discord.py** / **Pycord** (for Discord API interactions)
- **Wavelink / Lavalink** (for audio streaming)

## 🚀 How to Host

1. **Clone the Repository**: 
   ```bash
   git clone [https://github.com/YourUsername/YourMusicBot.git](https://github.com/YourUsername/YourMusicBot.git)

## 🌹 How to Use
1. **Create config.py to the main path (Discord-Bot-Music-main)**: 
2. **Paste this from your config.py**
   ```bash
   TOKEN = "Your-Discord-Bot-Token"
3. **Install dependency libraries:**
   ```bash
   pip install -r requirements.txt
   winget install ffmpeg
4. **Run**
    ```
    run.bat
