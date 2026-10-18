# Music Shelf

A Windows desktop wrapper for your existing yt-dlp executable. Download YouTube Liked videos and SoundCloud likes as MP3s with best VBR quality (`--audio-quality 0`), embedded metadata, and JPG cover art.

## Start

Double-click **[dist/MusicShelf.exe](dist/MusicShelf.exe)**. Python does not need to be installed to run the EXE. The app uses the existing yt-dlp, ffmpeg, ffprobe, Node.js, and Chrome on this computer; it does not bundle or replace them.

1. Choose the destination folder.
2. For YouTube, click **Connect** and sign in to your Google account in the separate, normal Chrome window. After you can see your signed-in YouTube account, **close that Chrome window**, then click **Continue** in Music Shelf. This is a one-time setup unless the session expires. Debugging is disabled throughout the human sign-in.
3. Click **Download from YouTube** or **Download from SoundCloud**.

If you click Download before connecting YouTube, the sign-in window opens automatically. Sign in, close that window, and click Continue; the download then resumes. Your usual Chrome windows can remain open. SoundCloud public likes needs no login.

Defaults:

- YouTube: `https://www.youtube.com/playlist?list=LL` (your signed-in account's Liked videos).
- SoundCloud: `https://soundcloud.com/valentin-serebryakov/likes`.
- yt-dlp: `C:\Users\sereb\main_files\coding\scripts\yt-dlp.exe`.
- ffmpeg/ffprobe: the same `scripts` folder.
- Node.js and Chrome: detected automatically.

**Settings** lets you change these paths and URLs. A separate YouTube music playlist is supported: paste its full `https://www.youtube.com/playlist?list=...` link. Public playlists can use **No login (public playlist)**. Firefox cookie extraction and an existing Netscape cookies file are optional fallbacks; cookies-file mode uses a temporary copy and leaves the original alone. For private SoundCloud tracks, enable its Chrome sign-in option in Settings.

## The queue

Songs appear while the playlist is read. The app then downloads them one at a time with **Queued**, **Downloading**, **Processing**, **Downloaded**, **Error**, or **Cancelled** status. Progress includes percentage, transfer speed, and estimated time when available. **Downloaded** means the final MP3 exists and yt-dlp completed successfully, including conversion and metadata processing.

- **Stop** cancels the current process and its ffmpeg children. Partial downloads stay on disk for yt-dlp to resume on the next run.
- **Retry errors** retries the failed songs in the current queue.
- Double-click a song for the full error, source link, or **Play MP3**.
- **Activity** shows yt-dlp warnings and diagnostics. A playlist that was only partly read is explicitly reported.
- Download history in the destination's `.music-shelf/history.json` skips MP3s already saved by this app. Deleted files download again. Choosing a different destination starts an independent collection.
- Filenames are `Title [track ID].mp3`, with Unicode supported. The ID prevents different songs with identical titles from colliding. Tracks downloaded with older title-only commands are not automatically imported into history.

## Sign-in and storage

Chrome's current Windows cookie encryption makes reading its usual profile unreliable. Music Shelf uses its own persistent Chrome profile, following [Chrome's separate-profile guidance](https://developer.chrome.com/blog/remote-debugging-port). Human sign-in happens in normal Chrome with no automation or debugging flags. Google may reject automated sign-in browsers, as described in [Google's supported-browser guidance](https://support.google.com/accounts/answer/7675428?hl=en).

After the sign-in window is closed, Music Shelf briefly opens the same profile in the background at `about:blank` and uses the local DevTools interface to read the existing cookies. It does not navigate to or interact with a sign-in page during this step. Subsequent downloads repeat this cookie-read step automatically; no extension or manual cookie export is needed.

The app stores settings and the isolated Chrome profile in `%LOCALAPPDATA%\MusicShelf`. Authentication stays in Chrome's profile. Only YouTube/Google or SoundCloud cookies are exported into a temporary per-run file, which is deleted on success, failure, or cancellation. The local debugging interface is enabled only for the brief background cookie-read step, and that browser closes after cookies have been collected. Cookie values are never shown in Activity. Cancelling sign-in stops the app's wait; close the normal sign-in window yourself.

The app cannot bypass YouTube or SoundCloud restrictions. Removed/private tracks, account challenges, and service changes appear as errors. Reconnect if sign-in expires. If yt-dlp breaks after a platform change, update your existing executable with `yt-dlp -U`; the app uses the new version on the next run. See [yt-dlp's documentation](https://github.com/yt-dlp/yt-dlp) and [cookie guidance](https://github.com/yt-dlp/yt-dlp/wiki/Extractors#exporting-youtube-cookies).

## Develop and build

Python 3.10+ with Tkinter is required for development.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe main.py
.\build.ps1
```

`build.ps1` creates the code-drawn icon, runs the tests, and builds `dist\MusicShelf.exe` with PyInstaller. `run.ps1` opens the EXE or the development app. Set `MUSIC_SHELF_HOME` to use a different settings/profile directory for testing or portable use.

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The GUI and download engine use separate threads. The engine calls your existing yt-dlp EXE with argument lists, never shell commands. MP3 completion is detected using yt-dlp's structured `after_move` output, rather than guessing from download progress.
