from . import config, events

cfg = config.load()
events.emit("ready", f"voice={cfg['voice']} engine={cfg['speech_engine']}")
print("ready")
