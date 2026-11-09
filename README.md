# RockWizard

Drop in an MP3 or WAV and get a Clone Hero song folder: the song split into guitar, bass, drums and
vocals, plus an automatic first-draft chart on all four difficulties.

## Using it
1. **First time only:** double-click `setup.bat` (needs Python from python.org; downloads a few GB,
   including PyTorch with NVIDIA GPU support). Everything installs into a private `.venv` folder,
   so your other Python programs are not affected.
2. Double-click `run_app.bat`.
3. Drop a song on the window (or click it), check the artist and title, press **Make my song**.
4. When it finishes you can listen to each separated instrument, then open the song folder.
   Copy that folder into your Clone Hero songs folder (or point **Save to** straight at it).

A graphics card makes it much faster (about 11 seconds for 40 seconds of music on an RTX 4070 Ti).

## Tidy song names
**Settings > Scan songs folder** finds song folders named like `Jimi Hendrix - Voodoo Child (Slight Return)
(Live) (Harmonix)` and proposes `Jimi Hendrix - Voodoo Child`. You tick the ones you want, press
**Rename selected**, and can **Undo last rename**. It also cleans the `name =` line in each `song.ini`.
Folders that would collide with an existing name are skipped. It never touches song files or
`song.ini` artist/album.

## What comes out
`<Artist - Title>/` containing `guitar.ogg` (everything that is not drums, bass or vocals), `bass.ogg`,
`drums.ogg`, `vocals.ogg`, `notes.mid` and `song.ini`.

The chart is a rough draft: tempo is detected as a single steady BPM, guitar and bass notes come from
onset and pitch detection spread over the five frets, and drums are labelled kick, snare, hi-hat and
crash by frequency band. Expect to tidy it in Moonscraper (use File > Load on `notes.mid`) and fix the
BPM markers and offset by hand, especially for songs with tempo changes.

## How it works
`chc/pipeline.py` runs: ffmpeg decode -> Demucs `htdemucs_ft` (`chc/worker.py`, separate process so it
can be cancelled) -> `chc/analysis.py` (tempo, onsets, pitch, drum bands) -> `chc/chart.py` (MIDI
writer, difficulty reduction). `app.py` is the interface (customtkinter + tkinterdnd2 for drag-and-drop).

MIDI note numbers used (verified against TheNathannator/GuitarGame_ChartFormats):
- Guitar/bass Expert 96-100 green..orange (Hard 84, Medium 72, Easy 60 base).
- Drums Expert 96 kick, 97 red, 98 yellow cymbal, 99 blue, 100 green cymbal (same offsets per difficulty).
- Tracks are named `PART GUITAR`, `PART BASS`, `PART DRUMS`, plus an `EVENTS` track.

## Advanced command-line tools
`advanced/separate.py` (batch Demucs into folders) and `advanced/midi_to_ch.py` (convert a MIDI from
Ableton into Clone Hero MIDI). They are separate from the app and need `python -m pip install mido`.
