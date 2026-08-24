"""Reference-edit reconstruction: cuts, source matching, framing. Rules encoded from user corrections.
Proxies: ffmpeg -i X -vf scale=-2:480 -c:v libx264 -crf 28 -preset veryfast -c:a aac -b:a 96k X_proxy.mp4
For source B ALWAYS: -map 0:v:0 -map 0:a:0 -ac 2 -sn   (downmix 5.1; Premiere chokes on surround). NO -r flag."""
import numpy as np, cv2, subprocess
from scipy.io import wavfile
from scipy import signal

def extract_audio(video, wav, sr=8000):
    subprocess.run(['ffmpeg','-y','-v','error','-i',video,'-ac','1','-ar',str(sr),'-vn',wav], check=True)

def load_band(wav, sr=4000, band=(300,1900)):
    _, x = wavfile.read(wav); x = x.astype(np.float32)
    if x.ndim > 1: x = x.mean(1)
    x = signal.decimate(x, 2, zero_phase=True)
    sos = signal.butter(4, band, btype='band', fs=sr, output='sos')
    return signal.sosfiltfilt(sos, x).astype(np.float32)

def ncc_search(window, haystack, sr):
    """Energy-masked normalized cross-correlation. Silence CANNOT win (learned the hard way, twice)."""
    w = window - window.mean(); nw = np.linalg.norm(w)
    L = len(w)
    if nw < 1e-3 or L >= len(haystack): return None, 0.0
    N = 1 << int(np.ceil(np.log2(len(haystack) + L)))
    corr = np.fft.irfft(np.fft.rfft(haystack, N) * np.fft.rfft(w[::-1], N), N)[:len(haystack)]
    cs = np.cumsum(np.concatenate([[0.0], haystack.astype(np.float64)**2]))
    e = np.sqrt(np.maximum(cs[L:] - cs[:-L], 1e-6))
    v = corr[L-1:L-1+len(e)] / (e * nw)
    emed = np.median(e[e > 1.0]) if (e > 1.0).any() else 1.0
    v[e < 0.25 * emed] = -1
    t = int(np.argmax(v))
    return t / sr, float(min(v[t], 1.0))

def visual_cuts(video, thresh_mad=12, min_gap=5):
    cap = cv2.VideoCapture(video); fps = cap.get(cv2.CAP_PROP_FPS)
    frames = []
    while True:
        ok, f = cap.read()
        if not ok: break
        frames.append(cv2.cvtColor(cv2.resize(f, (48, 86)), cv2.COLOR_BGR2GRAY).astype(np.float32))
    d = np.array([0] + [np.mean(np.abs(frames[i]-frames[i-1])) for i in range(1, len(frames))])
    med = np.median(d); mad = np.median(np.abs(d - med))
    thr = max(med + thresh_mad*mad, 18.0)
    cuts = [0]
    for i in range(1, len(d)):
        if d[i] > thr and i - cuts[-1] >= min_gap: cuts.append(i)
    return fps, len(frames), [c/fps for c in cuts[1:]], d

def segment_offsets(ts, offs, confs, jump=0.12, minconf=0.5):
    """Group fine-pass offsets into runs; a run boundary = candidate cut.
    RULE: offset steps < 0.25s are NOT cuts unless a visual cut confirms."""
    d = offs - ts
    segs = [[0]]
    for i in range(1, len(ts)):
        if not np.isfinite(d[i]) or confs[i] < minconf: segs[-1].append(i); continue
        prev = [j for j in segs[-1][-6:] if np.isfinite(d[j]) and confs[j] >= minconf]
        if prev and abs(d[i] - np.median(d[prev])) > jump: segs.append([i])
        else: segs[-1].append(i)
    out = []
    for s in segs:
        g = [j for j in s if np.isfinite(d[j]) and confs[j] >= minconf]
        if len(g) < 2: continue
        out.append((ts[s[0]], ts[s[-1]], float(np.median(d[g])), float(np.mean(confs[g]))))
    return out

def internal_cuts(video, t0, t1, min_gap=0.2):
    """B's own camera cuts in [t0,t1]. Segments SPLIT here; source in-points SNAP here (<=0.2s)."""
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_MSEC, (t0-0.5)*1000)
    prev = None; dd = []
    while True:
        ok, f = cap.read()
        if not ok: break
        t = cap.get(cv2.CAP_PROP_POS_MSEC)/1000
        if t > t1 + 0.5: break
        g = cv2.cvtColor(cv2.resize(f, (64, 35)), cv2.COLOR_BGR2GRAY).astype(np.float32)
        if prev is not None and t >= t0: dd.append((t, float(np.mean(np.abs(g - prev)))))
        prev = g
    if not dd: return []
    v = np.array([x for _, x in dd]); med = np.median(v); mad = np.median(np.abs(v - med))
    thr = max(med + 10*mad, 16)
    cuts = []
    for t, x in dd:
        if x > thr and (not cuts or t - cuts[-1] > min_gap): cuts.append(round(t, 3))
    return cuts

def recover_crop(frameA_band, frameB, m_range=(0.2, 3.4), steps=30):
    """Template-match A's video band into B across zoom. EXTEND the sweep if result pins at an edge.
    m = A pixels per B pixel. Weak scores -> prefer face/motion-salient subject instead."""
    ga, gb = frameA_band, frameB
    best = (None, -1)
    for m in np.linspace(*m_range, steps):
        h2, w2 = int(ga.shape[0]/m), int(ga.shape[1]/m)
        if w2 >= gb.shape[1] or h2 >= gb.shape[0] or w2 < 30: continue
        res = cv2.matchTemplate(gb, cv2.resize(ga, (w2, h2)), cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(res)
        if mv > best[1]: best = ((m, ml[0], ml[1], w2, h2), mv)
    return best

FACE = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
PROF = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_profileface.xml')
def faces(frame):
    g = cv2.equalizeHist(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)); out = []
    for cas, flip in [(FACE, False), (PROF, False), (PROF, True)]:
        src = cv2.flip(g, 1) if flip else g
        for (x, y, w, h) in cas.detectMultiScale(src, 1.1, 5, minSize=(24, 24)):
            if flip: x = g.shape[1] - x - w
            out.append((x + w/2, y + h/2, w*h))
    return out

# Framing constants (user-calibrated)
WINDOW = dict(x=42, y=555, w=998, h=1037)          # Petrol template transparent window
FACE_ANCHOR = (591.0, 902.0)                        # where faces land in the 1080x1920 sequence
def cover_scale(src_h): return round((WINDOW['h'] + 7) / src_h * 100, 1)  # user's zoom taste

def fcp7_xml(clips, src_name, src_w, src_h, template_png, seq_name, total_frames):
    """Emit importable XML. clips: [{tl_in,tl_out,b_in,b_out,px}] frames @ 25fps.
    timebase 25 ntsc FALSE is the ONLY structure with verified working audio import."""
    SCALE = cover_scale(src_h); PYc = 1073.5
    vit, ait = [], []
    for i, c in enumerate(clips):
        fr = (f'<file id="fileB"><name>{src_name}</name><pathurl>file://localhost/{src_name}</pathurl>'
              f'<rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate><media><video><samplecharacteristics>'
              f'<width>{src_w}</width><height>{src_h}</height></samplecharacteristics></video>'
              f'<audio><channelcount>2</channelcount></audio></media></file>') if i == 0 else '<file id="fileB"/>'
        mo = (f'<filter><effect><name>Basic Motion</name><effectid>basic</effectid><effectcategory>motion</effectcategory>'
              f'<effecttype>motion</effecttype><mediatype>video</mediatype>'
              f'<parameter authoringApp="PremierePro"><parameterid>scale</parameterid><name>Scale</name>'
              f'<valuemin>0</valuemin><valuemax>1000</valuemax><value>{SCALE}</value></parameter>'
              f'<parameter authoringApp="PremierePro"><parameterid>center</parameterid><name>Center</name>'
              f'<value><horiz>{(c["px"]-540)/1080.0:.4f}</horiz><vert>{(PYc-960)/1920.0:.4f}</vert></value></parameter>'
              f'</effect></filter>')
        base = (f'<name>B shot {i+1}</name><enabled>TRUE</enabled><rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>'
                f'<start>{c["tl_in"]}</start><end>{c["tl_out"]}</end><in>{c["b_in"]}</in><out>{c["b_out"]}</out>')
        vit.append(f'<clipitem id="v{i+1}">{base}{fr}{mo}</clipitem>')
        ait.append(f'<clipitem id="a{i+1}">{base}<file id="fileB"/></clipitem>')
    tm = (f'<clipitem id="t1"><name>{template_png}</name><enabled>TRUE</enabled>'
          f'<rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate><start>0</start><end>{total_frames}</end>'
          f'<in>0</in><out>{total_frames}</out><file id="fileT"><name>{template_png}</name>'
          f'<pathurl>file://localhost/{template_png}</pathurl><rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate>'
          f'<duration>{total_frames+50}</duration><media><video><samplecharacteristics><width>1080</width>'
          f'<height>1920</height></samplecharacteristics></video></media></file></clipitem>')
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n<xmeml version="4">'
            f'<sequence id="s1"><name>{seq_name}</name><duration>{total_frames}</duration>'
            f'<rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate><media><video><format><samplecharacteristics>'
            f'<width>1080</width><height>1920</height><pixelaspectratio>square</pixelaspectratio>'
            f'<rate><timebase>25</timebase><ntsc>FALSE</ntsc></rate></samplecharacteristics></format>'
            f'<track>{"".join(vit)}</track><track>{tm}</track></video>'
            f'<audio><track>{"".join(ait)}</track></audio></media></sequence></xmeml>')
