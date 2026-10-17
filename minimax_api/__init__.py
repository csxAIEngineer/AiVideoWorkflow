"""MiniMax 官方客户端。旁白走 speech-2.8-hd，背景音乐走 music-3.0。

地址：https://api.minimaxi.com/v1
鉴权：Authorization: Bearer <MINIMAX_API_KEY>

旁白：POST /t2a_v2，模型 speech-2.8-hd。音色用 MINIMAX_VOICE_ID。
没配音色时先拉系统音色，取一条中文音色，并写进 .env.example。

音乐：POST /music_generation，模型 music-3.0，is_instrumental 为 true，不传歌词。
"""

from minimax_api.client import (
    MUSIC_MODEL,
    SPEECH_MODEL,
    MiniMaxClient,
    MiniMaxError,
    choose_chinese_voice,
    remember_voice_example,
)

__all__ = [
    "MUSIC_MODEL",
    "SPEECH_MODEL",
    "MiniMaxClient",
    "MiniMaxError",
    "choose_chinese_voice",
    "remember_voice_example",
]
