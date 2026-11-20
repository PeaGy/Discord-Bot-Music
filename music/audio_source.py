"""Discord audio sources shared by cached and temporary tracks."""

import discord


OPUS_PREROLL_FRAMES = 8  # 8 x 20 ms = 160 ms để ổn định nhịp gửi lúc bắt đầu
OPUS_SILENCE_PACKET = b"\xF8\xFF\xFE"


class OpusPrerollAudioSource(discord.AudioSource):
    """Phát vài frame Opus im lặng trước khi đọc file thật.

    FFmpeg được khởi động ngay khi tạo ``source`` nên khoảng đệm này cho tiến trình
    đủ thời gian nạp packet đầu tiên, tránh làm audio thread trễ nhịp 20 ms của
    Discord. Nguồn vẫn là Opus xuyên suốt và không bị encode lại.
    """

    def __init__(self, source, frames=OPUS_PREROLL_FRAMES):
        if not source.is_opus():
            raise TypeError("OpusPrerollAudioSource yêu cầu nguồn Opus.")
        self.source = source
        self.remaining_frames = max(0, int(frames))

    def read(self):
        if self.remaining_frames > 0:
            self.remaining_frames -= 1
            return OPUS_SILENCE_PACKET
        return self.source.read()

    def is_opus(self):
        return True

    def cleanup(self):
        self.source.cleanup()
