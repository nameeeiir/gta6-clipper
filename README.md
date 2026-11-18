# GTA 6 live clipper

Watches GTA VI streams on Twitch, detects chat spikes, cuts the moment that caused them, edits a
vertical short for retention, and posts it to YouTube Shorts, TikTok and Instagram Reels while
the stream is still live. In the offline live-capture test, the rendered clip was ready about 35s
after the moment, without transcription or Claude. Those add roughly 10 to 40s depending on the
hardware, and the uploads and each platform's processing come on top of that.

```
chat (1 connection, all channels) ─► per-channel spike detector ─┐
capture (per channel, lossless ring buffer) ─────────────────────┤
                                                                  ▼
  extract moment ─► find payoff in audio ─► transcribe ─► Claude: worth posting? cut? title?
                 ─► render 9:16 (captions, zoom, SFX, voice FX) ─► YouTube + TikTok + Instagram in parallel
```

## What each stage does

**Detection.** Every chat message is scored: hype emotes (KEKW, OMEGALUL, Pog, monkaS, 💀, ???),
"clip it" requests, all caps and emote-only messages count more. Each chatter's contribution per
second is capped, and no single chatter may be more than 25% of a spike, so one spammer or bot
can't trigger a clip. Each channel keeps its own slow rolling baseline. A spike needs a high
z-score, a rate at least 2x normal, and at least 5 distinct chatters. The same settings work for
a 300-viewer stream and a 100k-viewer one. The chat mood (funny, hype, shock, tense, fail)
shapes the title and hook.

**Picking the moment.** Chat reacts about 4 seconds after the event. The picker searches the
audio just before the chat surge for the loudest sudden jump (a scream, explosion or crash) and
anchors the clip there. It then aligns the cut to sentence boundaries from a word-level Whisper
transcript: about 14s of setup, and the clip ends once the reaction finishes. Claude
(`claude-opus-5-5`) then sees the transcript and chat. It rejects spikes with nothing happening
on stream (raids, sub trains, chat memes), adjusts the cut, and writes the hook text, title,
description and hashtags. Without an API key, template titles are used.

**Editing.** Everything is done in a single ffmpeg pass:
- jump cuts on dead air (never within the payoff)
- 1.07x speed-up
- a 9:16 layout: blurred background with large gameplay, facecam-over-gameplay split, or full crop
- a hook text card for the first ~3s
- word-by-word highlighted captions
- a punch-in zoom, camera shake and white flash on the payoff
- a synthesized sub-boom, an echo tail and a bass hit on the impact (no copyrighted SFX)
- voice presence EQ and compression, normalized to -14 LUFS
- a progress bar and a `twitch.tv/<streamer>` credit

**Scaling to many streamers.** Capture is `-c copy` (no re-encoding), so each extra streamer
costs about 3.5 Mbps and a few percent of one core. All channels share one chat connection, one
Whisper model and a render queue that runs the strongest spikes first. Rendering is the main
cost: with libx264 on 4 cores, a 20s clip takes about 12s. Raise `edit.render_workers` on bigger
machines, or set `edit.encoder: h264_nvenc` with an NVIDIA GPU.

## Easiest setup (no coding needed)

**Windows:** double-click `SETUP-WINDOWS.bat`. It installs Python and ffmpeg if you don't have
them, installs the clipper, then asks you to paste your Twitch keys (it walks you through getting
them) and checks they work before saving them. When it says ALL SET, double-click
`START-WINDOWS.bat` to start clipping.

**Mac / Linux:** open Terminal in this folder and run `bash setup.sh`, then `bash start.sh`.

**Before GTA 6 is out (Nov 19, 2026)** there are no GTA VI streams to clip. To see it work on live
GTA 5 streams instead, double-click `TEST-WITH-GTA5.bat` (Mac: `bash start.sh --game "Grand Theft Auto V"`).

**Clip specific streamers** (whatever category they're in, e.g. IRL or Just Chatting): double-click
`WATCH-STREAMER.bat` and type their Twitch name, like `ishowspeed`
(Mac: `bash start.sh --any-category --channels ishowspeed`).

Clips land in `data/clips/`. Nothing is posted anywhere until you connect upload accounts and
turn posting on (see below). Run setup again any time to change your keys.

## Setup (manual)

Requires Python 3.10+ and ffmpeg 5 or newer.

```bash
pip install -e .
```

```bash
cp config.example.yaml config.yaml
```

```bash
cp .env.example .env
```

Fill in `.env` (see the comments in it), then connect your upload accounts once:

```bash
python -m clipper auth youtube
```

```bash
python -m clipper auth tiktok
```

```bash
python -m clipper auth instagram --token <short-lived user token>
```

Then check that everything is in place:

```bash
python -m clipper check
```

## Running

Start in dry-run mode: clips render to `data/clips/<id>/` (the video plus `meta.json` with the
cut, title, score and chat sample), but nothing is posted.

```bash
python -m clipper run
```

Watch specific streamers only:

```bash
python -m clipper run --channels streamer1 streamer2
```

Post for real (or set `publish.dry_run: false`):

```bash
python -m clipper run --post
```

**Backtest on a past stream** before going live. Download a VOD and its chat (for example with
TwitchDownloader), then run the whole pipeline offline to tune the thresholds:

```bash
python -m clipper replay --video vod.mp4 --chat chat.json --channel streamer --name Streamer --limit 5
```

Recent clips and their status:

```bash
python -m clipper status
```

## Tuning

| Want | Change |
|---|---|
| Fewer, bigger moments | raise `detector.z_enter` (3 → 4) and `detector.min_ratio` |
| More clips | lower them, and lower `detector.cooldown_s` |
| Cut starts too early or late | `moment.target_pre_s` (and `detector.reaction_lag_s` if the anchor is off) |
| Facecam-on-top layout | `streamers.<name>.facecam: [x, y, w, h]` as fractions of the frame |
| Faster on CPU | `transcribe.model: base.en`, `edit.preset: superfast` |

## Platform rules you need to know

- **TikTok:** until your app passes TikTok's audit, API posts are forced to private
  (`SELF_ONLY`) and only work for private accounts. The clipper detects this and posts privately
  instead of failing. TikTok also caps API posts per creator per day (about 15).
- **YouTube:** uploads consume your Google Cloud project's Data API quota (10,000 units/day by
  default). Google has changed the per-upload cost recently, so check the quota page in Cloud
  Console and request an increase if you'll post a lot.
- **Instagram:** needs a Business or Creator account linked to a Facebook Page, an app using
  Facebook Login for Business, and the `instagram_content_publish` permission. API publishing is
  capped per account per 24h. Long-lived user tokens expire after ~60 days, so re-run
  `auth instagram` or use a system-user token.
- **Rights:** you are reposting other people's streams. Get the streamers' permission. Many
  clip channels operate under an agreement or revenue share. Reused-content policies on all
  three platforms also penalize low-effort reposts, which is part of why every clip gets real
  edits and a credit.

## Layout

```
clipper/
  chat.py          one IRC connection for every channel
  twitch_api.py    Helix: find live GTA VI streams
  capture.py       streamlink → ffmpeg ring buffer, wall-clock aligned
  detector.py      spike detection
  media.py         audio envelope, impact finder, silence finder
  transcribe.py    faster-whisper word timestamps
  llm.py           Claude quality gate, cut and metadata
  moment.py        spike → exact cut plan
  captions.py      ASS captions, hook and credit
  editor.py        the ffmpeg render
  publishers/      youtube.py, tiktok.py, instagram.py
  pipeline.py      live orchestration + replay mode
tests/             detector, captions, uploads (mock APIs), Claude call (mock), end-to-end replay
```
