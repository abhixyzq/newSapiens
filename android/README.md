# Voice memory — Wear OS + Android

Tap record on the watch → the recording is sent to the phone → the phone uploads it to the backend
(`POST /api/process`) → transcript, summary, decisions and tasks are stored on the phone and searchable.
Audio is deleted from the watch once the phone has stored it, and from the phone once the result is saved.

```
wear/    Watch app: tile + app screen, foreground mic service (.m4a), sends files over the Data Layer
mobile/  Phone app: receives files, WorkManager upload, Room (+FTS search), calendar push, settings
```

## Backend
In `.env` (see `../.env.example`): `API_TOKEN=<long random string>`, `STT_PROVIDER=deepgram` + `DEEPGRAM_API_KEY`
(or `openai`), `LLM_PROVIDER=anthropic` + `LLM_MODEL` + `ANTHROPIC_API_KEY`. Run `uvicorn app.main:app --host 0.0.0.0`
behind HTTPS (release builds refuse plain http; debug builds allow it for a LAN address).

## Build & install
```bash
export JAVA_HOME=<JDK 17–21> ANDROID_HOME=~/Library/Android/sdk
./gradlew :mobile:assembleDebug :wear:assembleDebug :mobile:testDebugUnitTest
adb install mobile/build/outputs/apk/debug/mobile-debug.apk                 # phone
adb -s <watch-ip:port> install wear/build/outputs/apk/debug/wear-debug.apk  # watch (Developer options → Wireless debugging)
```
Phone and watch share one `applicationId` and must be signed with the same key (debug builds on one machine are).

## First run
1. Phone app → Settings → backend URL + API token.
2. Watch: open the app once (grants the microphone), add the **Voice memory** tile, tap *Record* / *Stop*.
3. The capture shows up on the phone within a minute; tasks with a certain date can be added to the calendar.

## Verified vs not
Verified on an emulator: phone app (Room/FTS open, upload → backend → stored result, retry, search, audio deletion),
watch recorder service (valid AAC `.m4a`, start/stop), unit tests for parsing/search/date logic.
**Not verified:** a real watch↔phone Data Layer transfer, the tile on a real Wear OS device, calendar insertion
(emulator had no calendar account), real Deepgram/Claude responses.
