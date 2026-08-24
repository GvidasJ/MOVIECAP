# Premiere Shorts Automation — Project Knowledge

Tool for automating meme-style vertical Shorts in Premiere Pro by direct .prproj
file surgery. Built and proven across 3 complete scenes with the user
(Lithuanian, edits TV footage like Top Gear / Grand Tour / Borat into 9:16 shorts).

## WORKFLOW (order is law)
1. User provides A (reference cut) + B (full source). Make 480p proxies, NO -r flag.
   B proxy command must include `-map 0:v:0 -map 0:a:0 -ac 2 -sn` (5.1 audio broke
   Premiere silently once; downmix to stereo ALWAYS).
2. Read B's REAL resolution from ffprobe or the .prproj FrameRect — NEVER assume
   (1080p assumption broke framing twice; B was 569x321 once, 1280x720 once).
3. Reconstruct cuts (audio-primary) + framing -> emit FCP7 XML (timebase 25
   ntsc FALSE — the ONLY structure with working audio import; timebase 24 failed).
4. User imports, fixes, reaches PICTURE LOCK. Only then:
5. Captions: user transcribes in Premiere (throwaway) -> Create Captions ->
   Upgrade Caption to Graphic -> Save As -> tool styles them.
   Caption texts come from the reference's burned-in captions (OCR/vision) or
   Whisper on the sequence audio — NEVER trust Premiere's ASR (it invented
   "hi Ben" for "HAMMOND!").

## .prproj FORMAT (all proven)
- gzip'd XML. TICKS = 254016000000 per second. Patch and re-gzip.
- Objects: ObjectID/ObjectRef (numeric, COLLIDE across classes — deref must
  prefer expected tag) and ObjectUID/ObjectURef (GUIDs, global/shared).
- Caption graphic = VideoClipTrackItem > ClipTrackItem { TrackItem(Start/End),
  ComponentOwner > Components chain, SubClip > VideoClip > Clip(InPoint/OutPoint) }.
- Upgraded captions have ONLY an AE.ADBE Text component; Motion (AE.ADBE Motion)
  and Vector Motion (AE.ADBE Graphic Group) must be cloned in from donors and the
  chain rewired to `Component Index 0/1/2 = Motion/VM/Text`.
- Text blob (Source Text param StartKeyframeValue, base64):
  uint64(total-12) + 752-byte style body + uint32(len) + utf8 text + NUL + pad to 4.
  Style body is FlatBuffers (magic 11223344): font @492, size 58.0 @624,
  fill color stored SPARSELY — only non-default channels (user's yellow #FFCE00
  stores only G=0xCE @~732; R=255,B=0 are schema defaults). Body is invariant
  across texts; splice is byte-exact (assets/style_body.pkl).
  BinaryHash attr: md5-as-GUID fabrication is accepted.
- Pop animation: Motion Scale keyframes `tick,value,0,0,0,0.1667,47.952,0.1667;
  tick+63567504000,value2,5,0,47.952,0.1667,0,0.3333;` — ticks are MEDIA time
  (rebase to each clip's InPoint; graphics zero is per-project, e.g.
  914457600000000 = 3600s at 25fps). Scale 88 -> 100 over ~6 frames.
- Keyframes generally: single text element <Keyframes>rec;rec;</Keyframes>,
  records mirror StartKeyframe field layout. Position keyframe format written
  once (records end `,5,4,0,0,0,0`) but NEVER user-verified.
- Whole-clip cloning: closure over ObjectRef (never clone ObjectURef targets —
  those are shared: masterclips, media), fresh ObjectIDs, append TrackItem ref,
  renumber Index. Proven for adding captions (user's edits preserved the clones).
- Deleting a clip: remove its TrackItem ref, renumber; orphans are tolerated.
- Merging captions: extend End + OutPoint by delta, delete the absorbed item.

## CRITICAL PITFALLS
- PATCH ONLY CLEAN BASES. A file that went through user-edit + Premiere re-save
  containing NEW-format text blobs became unpatchable ("project appears to be
  damaged") by every method incl. pure byte surgery; identical operations on the
  pre-edit base always open. Rebuild on the clean base with user values instead.
- Premiere re-save renumbers ObjectIDs; never reuse IDs across saves.
- "File Import Failure" dialog = they IMPORTED the prproj; direct File > Open is
  the correct path and is more tolerant.
- Retyped-in-new-Premiere text blobs are unreadable by the length-tail scanner;
  flag instead of guessing.
- Premiere audio conform: after import, wait for the bottom-right progress bar;
  "no audio + striped clips" = offline media -> Link Media, not Replace Footage.

## RECONSTRUCTION RULES (each learned from a user correction)
Cuts: band-pass 300-1900Hz, FFT NCC; coarse 1.2s windows then fine 0.35s/0.05s;
offset step > 0.12s = candidate cut BUT steps < 0.25s need visual confirmation
(false jump cut removed by user once). Energy-mask silent regions in any NCC
search (silence produced fake conf=1.0 matches twice). Snap source in-points onto
B's internal camera cuts within 0.2s. Split segments at B-internal cuts and frame
each sub-shot separately. +-3 frame visual boundary polish.
Framing: recover A's crop by template match of A's REAL video band (detect it —
A may be letterboxed with caption bands) into B with a WIDE zoom sweep (extend
when the estimate pins at sweep edge). Weak match -> bias to motion-salient or
face subject (template matching locks onto texture, not narrative subject).
Faces: Haar frontal+profile, nearest-to-previous continuity, pick face nearest
A's framing in multi-face shots. User's anchor: face lands at (591, 902) in the
1080x1920 sequence. Template: Petrol PNG 1080x1920, transparent window
x42 y555 w998 h1037. Zoom taste = displayed height ~= window height + 7px
(145% for 720p, 100.4% for 1040p, 364% for 321p). Vertical position is forced
~1073.5 by window cover; clamp positions so footage always covers the window.

## CAPTION TEXT RULES (user's style)
- Short chunks 1-4 words, first letter capitalized, strip . and , keep ? ! '
- The 1-4 word rule OVERRIDES reference chunking (user correction): a 5-word
  reference line rendered truncated ("Does not mean it's[ stolen]") — the text
  box clips long lines at Verdana-Bold 58. Re-chunk; keep chunks <= ~19 chars.
- Never end a caption on a be-verb (am/is/are/was/were/be) OR an article
  (a/an/the) — move the article to the FRONT of the next caption (user writes
  it lowercase: "a R*tardation?") or merge.
- Censor swears: user's current pattern = first letter + all asterisks
  ("f******", "f***"); the r-word historically "r*tard" (2nd char star) — ask.
- Reference burned captions are truth for timing/style but AUDIO is truth for
  completeness (references omit swears and small words like "because"/"right").
- Karaoke-style references: per-word highlight walk gives per-word timing but
  LAGS speech ~0.2-0.4s — lead it. Respect the reference's blank gaps.
- Captions ONLY after picture lock (user re-cut under finished captions once:
  2.7s drift).
- Acoustic placement: match each line's reference audio snippet against the
  sequence audio (rebuilt from the project's AUDIO track clips, not video) with
  silence masking; drop captions whose speech isn't found (conf < 0.55).

## STYLE CONSTANTS
Verdana-Bold ~58, fill #FFCE00, Text position param (0.17315, 0.58542),
Motion pos 540:1110 anchor 540:1000, VM scale 88, Motion scale 100 with the pop.
Donor assets in assets/ are the source of truth — splice, don't rebuild.
