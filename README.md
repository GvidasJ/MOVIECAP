# Shorts Automation Kit — Claude Code starter

Start Claude Code in this folder. CLAUDE.md carries the complete knowledge from
the chat sessions where every piece of this was built and proven: the .prproj
format, the reconstruction pipeline, the caption system, and every rule learned
from the user's manual corrections across three finished scenes.

## Setup
    pip install numpy scipy opencv-python openai-whisper
    # ffmpeg on PATH; tesseract optional (Claude reads caption frames directly)

## The two jobs
1. RECONSTRUCT: `A.mp4` (reference cut) + `B.mp4` (full source)
   -> proxies -> audio matching -> cuts + framing -> FCP7 XML for import.
   recon_lib.py has every primitive with the correction rules built in.
2. CAPTIONS (only after picture lock): user transcribes + upgrades to graphics
   in Premiere, saves; prproj_lib.py styles everything: blob splice, donor
   params, pop clones, retime, clone/delete. Texts from reference burned
   captions (read frames) or Whisper — never Premiere's ASR.

## Assets (the style DNA — do not regenerate, splice)
- assets/style_body.pkl      752-byte Verdana-Bold #FFCE00 style body
- assets/donor_motion.xml    Motion component + params incl. the 88->100 pop
- assets/donor_vm.xml        Vector Motion component + params
- assets/donor_params.pkl    22 Text param values + pop tick constants

## Iron rules (violating any of these cost real debugging hours)
- Read source resolution from the file. Never assume.
- Downmix source audio to stereo before Premiere sees it.
- Patch clean bases only; rebuild instead of patching Premiere re-saves.
- Captions after picture lock.
- File > Open, not Import, for generated .prproj files.
