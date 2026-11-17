# Cosmo

An always-on voice agent for the Mac. Say "Hey Cosmo" (or hold a key), ask for something, and Cosmo answers out loud and does the work: it runs Claude Code or Codex in the background, uses your Mac apps, controls your Android phone, and types dictation into any app.

Speech recognition, the voice and the wake word all run on your Mac. Only the request text goes to the AI model you pick.

## What it does

- **Talk to it.** Wake word ("Hey Cosmo") with a check that it is your voice, or a hold-to-talk key. Talk over it or say "stop" to cut it off.
- **Gets work done.** A fast front model answers and hands real work to background Claude Code (or Codex) agents, so you can keep talking while they run.
- **Uses Mac apps.** Opens apps, reads windows through the Accessibility API, presses buttons, types and uses menus, in the background. Password, banking and crypto apps are off limits.
- **Controls your Android phone.** Opens apps, reads the screen, taps, types and swipes over wireless adb. Developer mode is turned on only while Cosmo uses the phone and off again after two idle minutes, so bank apps keep working.
- **Dictation.** Hold a key (or double-tap to lock), talk, and the text is typed into whatever app has focus. Spoken commands like "new line", "period" and "scratch that", plus your own word list.
- **Mac app.** A native SwiftUI app with the conversation, running tasks, your Claude Code sessions, devices and settings, plus a small on-screen orb.
- **Phone app.** An Android companion for chat, Stop and approvals, paired from the Mac app with a QR code.

## Requirements

- macOS 26 on Apple silicon
- Python 3.12
- [Claude Code](https://docs.claude.com/en/docs/claude-code) logged in (Cosmo uses your Claude Code login), or the Codex CLI
- Xcode command line tools (to build the Mac app)
- Optional: `adb` (`brew install android-platform-tools`) and JDK 17 + Android SDK to build the phone app

## Setup

```bash
git clone https://github.com/shivexe/cosmo.git
cd cosmo
python3.12 -m venv ~/.cosmo/venv
~/.cosmo/venv/bin/pip install -e .
```

Build and install the Mac app (it starts the voice engine on launch):

```bash
./app/build.sh --install
```

Open Cosmo from Applications and grant Microphone and Accessibility when asked. Screen Recording is only needed for app screenshots.

Settings live in `~/.cosmo/config.json` and are editable from the app: your name, voice, keys, wake mode, brain (Claude or Codex), your Python path, and other machines.

### Phone (optional)

```bash
cd android && ./gradlew :app:assembleDebug
```

Then rebuild the Mac app so it bundles the APK. With the phone connected over adb once, use **Devices → Install on Phone** and **Pair Phone…**, and grant the settings permission once:

```bash
adb shell pm grant dev.notsg.cosmo android.permission.WRITE_SECURE_SETTINGS
```

### Your own speech model (optional)

`lab/` has the scripts used to fine-tune Whisper small.en with LoRA on your own dictation, and `wake/` trains the wake word and voice check on your recordings. Point `adapter_path` in the config at the trained adapter. Without it, Cosmo uses plain Whisper small.en.

## Layout

| Folder | What is in it |
|---|---|
| `src/cosmo/` | Voice engine: audio, wake word, speech to text, voice, brain, Mac and phone control, phone bridge |
| `app/` | Native macOS app (SwiftUI) |
| `android/` | Android companion app (Kotlin, Compose) |
| `lab/` | Speech model fine-tuning experiments |
| `wake/` | Wake word and voice check training |

## License

MIT
