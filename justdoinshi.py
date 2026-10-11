import asyncio, edge_tts

async def speak(text, out="out.mp3"):
    await edge_tts.Communicate(text, "en-US-AriaNeural").save(out)

asyncio.run(speak("Hello, I'm Zars."))