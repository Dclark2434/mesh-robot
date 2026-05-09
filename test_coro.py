import livekit.rtc as rtc
import inspect
import asyncio

print("IS CORO:", inspect.iscoroutinefunction(rtc.AudioSource.capture_frame))
