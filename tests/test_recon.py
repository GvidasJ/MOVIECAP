"""End-to-end reconstruction smoke test on synthetic footage with known ground truth.

B (source): 640x360 @25fps, 24s, 4 scenes (cuts at 6/12/18s), 5.1 noise audio.
A (reference): 480x854 vertical, 3 segments of B, each a different crop scaled into
a letterboxed video band (black caption bands above/below) — like a real reference.

Ground truth:
  segment 1: A[0.00, 3.00) = B[2.00, 5.00)   crop (100, 40, 320, 180)  -> offset 2.00
  segment 2: A[3.00, 5.48) = B[8.48, 10.96)  crop (200, 90, 240, 135)  -> offset 5.48
  segment 3: A[5.48, 8.28) = B[14.20, 17.00) crop (50, 20, 400, 225)   -> offset 8.72
"""
import sys, os, subprocess, struct
import numpy as np, cv2
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_scratch')
os.makedirs(OUT, exist_ok=True)
os.chdir(OUT)
import recon_lib as R

FPS, W, H, DUR = 25, 640, 360, 24
SR = 48000
rng = np.random.default_rng(7)
ok = lambda s: print(f'  PASS  {s}')

# ---- build B ----------------------------------------------------------------
SCENE_BG = [(40, 40, 130), (30, 120, 40), (120, 60, 30), (90, 30, 110)]
tex = [cv2.resize(rng.integers(0, 255, (18, 32), np.uint8), (W, H),
                  interpolation=cv2.INTER_NEAREST) for _ in range(4)]  # coarse per-scene texture

def frame_B(i):
    s = min(i // (6 * FPS), 3)
    f = np.empty((H, W, 3), np.uint8)
    f[:] = SCENE_BG[s]
    f = (0.5 * f + 0.5 * tex[s][..., None]).astype(np.uint8)
    x = int((i % (6 * FPS)) / (6 * FPS) * (W - 60))   # moving block
    cv2.rectangle(f, (x, 150), (x + 50, 200), (255, 255, 255), -1)
    cv2.putText(f, str(s + 1), (280, 210), cv2.FONT_HERSHEY_SIMPLEX, 4, (255, 255, 0), 8)
    return f

def write_video(path, frames, wav, extra_audio_args):
    p = subprocess.Popen(['ffmpeg', '-y', '-v', 'error',
        '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{frames[0].shape[1]}x{frames[0].shape[0]}',
        '-r', str(FPS), '-i', '-', '-i', wav] + extra_audio_args +
        ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20', '-shortest', path],
        stdin=subprocess.PIPE)
    for f in frames: p.stdin.write(f.tobytes())
    p.stdin.close(); assert p.wait() == 0, path

def write_wav(path, x):  # int16 stereo
    import scipy.io.wavfile as wf
    wf.write(path, SR, (np.clip(x, -1, 1) * 32000).astype(np.int16))

# speech-band-ish noise, amplitude modulated so it is unique everywhere
n = DUR * SR
env = np.interp(np.arange(n), np.arange(0, n, SR // 8), rng.uniform(0.15, 1.0, len(range(0, n, SR // 8))))
audio = rng.standard_normal(n).astype(np.float32) * env
audioB = np.stack([audio, audio * 0.85 + rng.standard_normal(n) * 0.05 * env], 1)

write_wav('Baud.wav', audioB)
framesB = [frame_B(i) for i in range(DUR * FPS)]
# 5.1 audio on purpose: the proxy rule must downmix it
write_video('B.mp4', framesB, 'Baud.wav',
            ['-af', 'pan=5.1|FL=c0|FR=c1|FC=0.3*c0|LFE=0.1*c0|BL=0.4*c0|BR=0.4*c1', '-c:a', 'aac'])

# ---- build A ----------------------------------------------------------------
SEGS = [  # (A start frame, n frames, B start frame, crop x,y,w,h)
    (0,   75, 50,  (100, 40, 320, 180)),
    (75,  62, 212, (200, 90, 240, 135)),
    (137, 70, 355, (50,  20, 400, 225)),
]
BAND_Y, BAND_H, AW, AH = 292, 270, 480, 854
framesA = []
for a0, nfr, b0, (cx, cy, cw, ch) in SEGS:
    for k in range(nfr):
        crop = framesB[b0 + k][cy:cy + ch, cx:cx + cw]
        band = cv2.resize(crop, (AW, BAND_H))
        f = np.zeros((AH, AW, 3), np.uint8)
        f[BAND_Y:BAND_Y + BAND_H] = band
        cv2.putText(f, 'CAPTION', (140, 700), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 206, 255), 3)
        framesA.append(f)
sl = [audioB[int(b0 / FPS * SR): int((b0 + nfr) / FPS * SR)] for a0, nfr, b0, _ in SEGS]
write_wav('Aaud.wav', np.concatenate(sl))
write_video('A.mp4', framesA, 'Aaud.wav', ['-c:a', 'aac'])

# ---- proxies exactly per the kit rules --------------------------------------
subprocess.run('ffmpeg -y -v error -i A.mp4 -vf scale=-2:480 -c:v libx264 -crf 28 '
               '-preset veryfast -c:a aac -b:a 96k A_proxy.mp4'.split(), check=True)
subprocess.run('ffmpeg -y -v error -i B.mp4 -map 0:v:0 -map 0:a:0 -ac 2 -sn '
               '-vf scale=-2:480 -c:v libx264 -crf 28 -preset veryfast '
               '-c:a aac -b:a 96k B_proxy.mp4'.split(), check=True)
ch = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries',
                     'stream=channels', '-of', 'csv=p=0', 'B_proxy.mp4'],
                    capture_output=True, text=True).stdout.strip()
assert ch == '2', f'B proxy channels = {ch}'
ok('proxies built per kit rules; 5.1 source downmixed to stereo')

# ---- audio matching: coarse NCC windows -> segment_offsets ------------------
R.extract_audio('A_proxy.mp4', 'A8k.wav'); R.extract_audio('B_proxy.mp4', 'B8k.wav')
a, b = R.load_band('A8k.wav'), R.load_band('B8k.wav')
sr, WIN, HOP = 4000, 1.2, 0.3
ts, offs, confs = [], [], []
t = 0.0
while t + WIN < len(a) / sr:
    off, conf = R.ncc_search(a[int(t * sr):int((t + WIN) * sr)], b, sr)
    ts.append(t); offs.append(np.nan if off is None else off); confs.append(conf)
    t += HOP
ts, offs, confs = map(np.array, (ts, offs, confs))
segs = R.segment_offsets(ts, offs, confs, jump=0.12, minconf=0.5)
truth = [2.00, 5.48, 8.72]
print(f'    recovered segments (t0, t1, offset, conf):')
for s in segs: print(f'      {s[0]:5.2f} {s[1]:5.2f}  offset {s[2]:6.3f}  conf {s[3]:.3f}')
assert len(segs) == 3, f'{len(segs)} segments'
for (t0, t1, d, cf), gt in zip(segs, truth):
    assert abs(d - gt) < 0.05, f'offset {d} vs truth {gt}'
    assert cf > 0.6
ok(f'audio NCC recovered all 3 segment offsets within 50ms of ground truth')

# ---- visual cuts ------------------------------------------------------------
# per the kit rules, cut detection runs on A's REAL video band, not the letterbox
by, bh = round(BAND_Y * 480 / AH / 2) * 2, round(BAND_H * 480 / AH / 2) * 2
subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', 'A_proxy.mp4',
                '-vf', f'crop=270:{bh}:0:{by}', '-an', 'A_band.mp4'], check=True)
fps, nfr, cutsA, _ = R.visual_cuts('A_band.mp4')
exp = [3.00, 5.48]
assert all(any(abs(c - e) <= 0.081 for c in cutsA) for e in exp), f'A cuts: {cutsA}'
ok(f'visual_cuts on A found the reference cuts {[round(c,2) for c in cutsA]} (truth {exp})')

icuts = R.internal_cuts('B_proxy.mp4', 0, DUR)
expB = [6.0, 12.0, 18.0]
assert all(any(abs(c - e) <= 0.081 for c in icuts) for e in expB), f'B cuts: {icuts}'
ok(f'internal_cuts on B found scene cuts {icuts} (truth {expB})')

# ---- framing recovery -------------------------------------------------------
capA = cv2.VideoCapture('A.mp4'); capA.set(cv2.CAP_PROP_POS_FRAMES, 25)
_, fA = capA.read()
band = cv2.cvtColor(fA[BAND_Y:BAND_Y + BAND_H], cv2.COLOR_BGR2GRAY)
gB = cv2.cvtColor(framesB[75], cv2.COLOR_BGR2GRAY)      # B @ 3.0s
(mm, x, y, w2, h2), score = R.recover_crop(band, gB)
gt_m = AW / 320
assert score > 0.7, f'weak match {score}'
assert abs(mm - gt_m) < 0.12 and abs(x - 100) <= 6 and abs(y - 40) <= 6, (mm, x, y)
ok(f'recover_crop: zoom {mm:.2f} (truth {gt_m:.2f}), crop at ({x},{y}) (truth (100,40)), score {score:.2f}')

assert not R.FACE.empty() and not R.PROF.empty()
assert isinstance(R.faces(framesB[0]), list)
ok('Haar face cascades load and run')

# ---- FCP7 XML emission ------------------------------------------------------
import xml.etree.ElementTree as ET
clips = []
for (t0, t1, d, cf) in segs:
    tl_in = int(round(t0 * FPS)); tl_out = int(round((t1 + HOP) * FPS))
    clips.append(dict(tl_in=tl_in, tl_out=tl_out,
                      b_in=tl_in + int(round(d * FPS)), b_out=tl_out + int(round(d * FPS)), px=540))
xmlout = R.fcp7_xml(clips, 'B.mp4', W, H, 'Petrol.png', 'SMOKE TEST', clips[-1]['tl_out'])
root = ET.fromstring(xmlout)
assert len(root.findall('.//video/track')) == 2 and len(root.findall('.//audio/track')) == 1
assert len(root.findall('.//video/track[1]/clipitem')) == 3
assert root.find('.//rate/timebase').text == '25' and root.find('.//rate/ntsc').text == 'FALSE'
sc = float(root.find('.//parameter[parameterid="scale"]/value').text)
assert sc == R.cover_scale(H) == 290.0
open('smoke_scene.xml', 'w').write(xmlout)
ok(f'FCP7 XML emitted + parses: 3 clips, timebase 25 ntsc FALSE, scale {sc} for {H}p source')

print('\nrecon_lib: ALL PASS')
