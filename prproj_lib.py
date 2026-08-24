"""Proven .prproj surgery library. All operations verified in production across 3 scenes.
GOLDEN RULE: only patch clean bases (pre-user-edit). Rebuild rather than patch re-saves."""
import gzip, re, base64, struct, hashlib, pickle, io, os, zlib
import xml.etree.ElementTree as ET

TICKS = 254016000000
POP_DELTA = 63567504000  # 6-frame pop duration in ticks
ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')

def load(path):
    """-> (xml_string, ET_root, by_id, by_uid, deref). by_id maps to LISTS (IDs collide)."""
    raw = gzip.open(path, 'rb').read().decode('utf-8')
    root = ET.fromstring(raw)
    by_id, by_uid = {}, {}
    for el in root.iter():
        if el.get('ObjectID'): by_id.setdefault(el.get('ObjectID'), []).append(el)
        if el.get('ObjectUID'): by_uid[el.get('ObjectUID')] = el
    def deref(el, prefer=None):
        if el is None: return None
        if el.get('ObjectRef') is not None:
            c = by_id.get(el.get('ObjectRef'), [])
            if prefer:
                for x in c:
                    if x.tag in prefer: return x
            return c[-1] if c else None
        if el.get('ObjectURef') is not None: return by_uid.get(el.get('ObjectURef'))
        return el
    return raw, root, by_id, by_uid, deref

def save(xml_string, path):
    ET.fromstring(xml_string)  # validate before writing
    with gzip.open(path, 'wb') as f: f.write(xml_string.encode('utf-8'))

def save_like(reference_prproj_bytes, xml_bytes, path):
    """Byte-surgery save: forge gzip header identical to the reference file's."""
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    d = comp.compress(xml_bytes) + comp.flush()
    hdr = reference_prproj_bytes[:10]
    out = hdr + d + struct.pack('<I', zlib.crc32(xml_bytes)) + struct.pack('<I', len(xml_bytes) & 0xFFFFFFFF)
    open(path, 'wb').write(out)

# ---- text blobs -------------------------------------------------------------
STYLE_BODY = pickle.load(open(os.path.join(ASSETS, 'style_body.pkl'),'rb'))

def make_blob(text, body=None):
    body = body or STYLE_BODY
    b = text.encode('utf-8'); n = len(b)
    total = 12 + len(body) + ((n+1+3)//4)*4
    return struct.pack('<Q', total-12) + body + struct.pack('<I', n) + b + b'\x00'*(total-12-len(body)-n)

def fab_hash(blob):
    h = hashlib.md5(blob).hexdigest()
    return f'{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}'

def read_blob_text(raw):
    """Scan from end for uint32 len + utf8 (old format). Returns None for Premiere's new format."""
    for o in range(len(raw)-8, 8, -1):
        n = struct.unpack_from('<I', raw, o)[0]
        if 0 < n <= len(raw)-o-4 and len(raw)-(o+4+n) <= 4:
            try:
                s = raw[o+4:o+4+n].decode('utf-8')
                if s and all(c.isprintable() for c in s): return s
            except Exception: pass
    return None

def harvest_body(raw):
    """Extract the invariant style body from a styled blob -> (body, text)."""
    for o in range(len(raw)-8, 8, -1):
        n = struct.unpack_from('<I', raw, o)[0]
        if 0 < n <= len(raw)-o-4 and len(raw)-(o+4+n) <= 4:
            try:
                s = raw[o+4:o+4+n].decode('utf-8')
                if s and all(c.isprintable() for c in s): return raw[8:o], s
            except Exception: pass
    return None, None

# ---- caption inventory ------------------------------------------------------
def find_captions(root, deref):
    """All caption graphics: dicts with st,en,inp,outp,vct,clip_id,clip_tag,tcid,pids,ncomps,text."""
    out = []
    for vct in root.iter('VideoClipTrackItem'):
        comps_el = deref(vct.find('.//ComponentOwner/Components'))
        if comps_el is None: continue
        tcomp = None; n = 0
        for c in comps_el.iter():
            if c.tag == 'Component' and c.get('ObjectRef'):
                comp = deref(c, prefer=('VideoFilterComponent',))
                if comp is None: continue
                n += 1
                if (comp.findtext('.//MatchName') or '') == 'AE.ADBE Text': tcomp = comp
        if tcomp is None: continue
        inner = vct.find('ClipTrackItem/TrackItem')
        clip = deref(deref(vct.find('ClipTrackItem/SubClip')).find('Clip'), prefer=('VideoClip',))
        params = [deref(p) for p in tcomp.find('.//Params')]
        v = (params[0].findtext('StartKeyframeValue') or '').strip()
        raw = base64.b64decode(v) if v else b''
        out.append({'st': int(inner.findtext('Start')), 'en': int(inner.findtext('End')),
                    'inp': int(clip.find('Clip').findtext('InPoint')),
                    'outp': int(clip.find('Clip').findtext('OutPoint')),
                    'vct': vct.get('ObjectID'), 'clip_id': clip.get('ObjectID'), 'clip_tag': clip.tag,
                    'tcid': tcomp.get('ObjectID'), 'pids': [p.get('ObjectID') for p in params],
                    'ncomps': n, 'text': read_blob_text(raw)})
    out.sort(key=lambda c: c['st'])
    return out

# ---- surgery primitives (string-level; anchor on unique full patterns) ------
SKV = r'<StartKeyframeValue\b[^>]*>[^<]*</StartKeyframeValue>|<StartKeyframeValue\b[^>]*/>'

def set_text(xml, cap, text):
    blob = make_blob(text); nb = base64.b64encode(blob).decode()
    m = re.search(rf'<ArbVideoComponentParam ObjectID="{cap["pids"][0]}"[^>]*>', xml)
    end = xml.index('</ArbVideoComponentParam>', m.start()); blk = xml[m.start():end]
    newel = '<StartKeyframeValue Encoding="base64" BinaryHash="'+fab_hash(blob)+'">'+nb+'</StartKeyframeValue>'
    blk, n = re.subn(SKV, lambda _: newel, blk, count=1); assert n == 1
    return xml[:m.start()] + blk + xml[end:]

def retime(xml, cap, a_sec, b_sec):
    stT, enT = int(round(a_sec*TICKS)), int(round(b_sec*TICKS))
    m = re.search(rf'<VideoClipTrackItem ObjectID="{cap["vct"]}"[^>]*>', xml)
    end = xml.index('</VideoClipTrackItem>', m.start()); blk = xml[m.start():end]
    blk, n1 = re.subn(r'<Start>\d+</Start>', f'<Start>{stT}</Start>', blk, count=1)
    blk, n2 = re.subn(r'<End>\d+</End>', f'<End>{enT}</End>', blk, count=1); assert n1 == n2 == 1
    xml = xml[:m.start()] + blk + xml[end:]
    m = re.search(rf'<{cap["clip_tag"]} ObjectID="{cap["clip_id"]}"[^>]*>', xml)
    end = xml.index(f'</{cap["clip_tag"]}>', m.start()); blk = xml[m.start():end]
    blk, n3 = re.subn(r'<OutPoint>\d+</OutPoint>', f'<OutPoint>{cap["inp"]+(enT-stT)}</OutPoint>', blk, count=1); assert n3 == 1
    return xml[:m.start()] + blk + xml[end:]

def apply_style(xml, cap, donor_params, motion_xml, vm_xml, next_id):
    """Clone Motion+VM (pop keyframes rebased to cap's InPoint), rewire chain, copy Text params."""
    dparams = pickle.load(open(os.path.join(ASSETS, 'donor_params.pkl'),'rb')) if donor_params is None else donor_params
    T0, T1 = dparams['pop_t0'], dparams['pop_t1']
    mroot = re.search(r'ObjectID="(\d+)"', motion_xml).group(1)
    vroot = re.search(r'ObjectID="(\d+)"', vm_xml).group(1)
    olds = [mroot] + re.findall(r'ObjectRef="(\d+)"', motion_xml) + [vroot] + re.findall(r'ObjectRef="(\d+)"', vm_xml)
    # fresh ids must clear the donor's own id range, or the sequential replace
    # in cl() corrupts the clone once next_id walks into it
    next_id[0] = max(next_id[0], max(int(o) for o in olds) + 1)
    idmap = {o: str(next_id[0]+i) for i, o in enumerate(olds)}; next_id[0] += len(idmap)
    def cl(s):
        for o, n in idmap.items():
            s = s.replace(f'ObjectID="{o}"', f'ObjectID="{n}"').replace(f'ObjectRef="{o}"', f'ObjectRef="{n}"')
        return s.replace(str(T0), str(cap['inp'])).replace(str(T1), str(cap['inp'] + (T1-T0)))
    ins = xml.rindex('</VideoFilterComponent>') + len('</VideoFilterComponent>')
    xml = xml[:ins] + '\n\t' + cl(motion_xml) + '\n\t' + cl(vm_xml) + xml[ins:]
    pat = rf'<Component Index="0" ObjectRef="{cap["tcid"]}"/>'
    assert len(re.findall(pat, xml)) == 1
    rep = (f'<Component Index="0" ObjectRef="{idmap[mroot]}"/>\n\t\t\t\t'
           f'<Component Index="1" ObjectRef="{idmap[vroot]}"/>\n\t\t\t\t'
           f'<Component Index="2" ObjectRef="{cap["tcid"]}"/>')
    xml = re.sub(pat, rep, xml)
    for idx, pid in enumerate(cap['pids']):
        if idx == 0: continue
        dv = dparams['text_param_values'][idx]
        m = re.search(rf'<(\w+ComponentParam) ObjectID="{pid}"[^>]*>', xml)
        tag = m.group(1); end = xml.index(f'</{tag}>', m.start()); blk = xml[m.start():end]
        if dv['sk'] is not None:
            blk = re.sub(r'<StartKeyframe>[^<]*</StartKeyframe>', f'<StartKeyframe>{dv["sk"]}</StartKeyframe>', blk, count=1)
        if dv['cv'] is not None and '<CurrentValue>' in blk:
            blk = re.sub(r'<CurrentValue>[^<]*</CurrentValue>', f'<CurrentValue>{dv["cv"]}</CurrentValue>', blk, count=1)
        xml = xml[:m.start()] + blk + xml[end:]
    return xml

def clone_caption(xml, template_cap, next_id):
    """Full closure clone (ObjectRef graph only; URef targets stay shared). Returns (xml, idmap)."""
    def get_block(x, oid):
        cands = []
        for m in re.finditer(rf'<(\w+) ObjectID="{oid}"[ >]', x):
            tag = m.group(1); end = x.index(f'</{tag}>', m.start()) + len(tag) + 3
            cands.append((oid, tag, x[m.start():end]))
        return cands[-1] if cands else None
    seen = set(); order = []; stack = [template_cap['vct']]
    while stack:
        oid = stack.pop()
        if oid in seen: continue
        seen.add(oid)
        blk = get_block(xml, oid)
        if blk is None: continue
        order.append(blk)
        for r in re.findall(r'ObjectRef="(\d+)"', blk[2]):
            if r not in seen: stack.append(r)
    idmap = {oid: str(next_id[0]+i) for i, (oid, _, _) in enumerate(order)}; next_id[0] += len(order)
    blocks = []
    for oid, tag, blk in order:
        s = blk
        for o, n in idmap.items():
            s = s.replace(f'ObjectID="{o}"', f'ObjectID="{n}"').replace(f'ObjectRef="{o}"', f'ObjectRef="{n}"')
        # duplicate <ClipID> GUIDs = "project appears to be damaged" in newer
        # Premiere; give clones fresh clip identity like Premiere itself would
        s = re.sub(r'<ClipID>([0-9a-f-]{36})</ClipID>',
                   lambda m: '<ClipID>' + fab_hash((m.group(1) + ':' + idmap[oid]).encode()) + '</ClipID>', s)
        blocks.append(s)
    ins = xml.rindex('</VideoClipTrackItem>') + len('</VideoClipTrackItem>')
    xml = xml[:ins] + '\n\t' + '\n\t'.join(blocks) + xml[ins:]
    am = re.search(rf'<TrackItem Index="\d+" (ObjectU?Ref)="{template_cap["vct"]}"/>', xml)
    refattr = am.group(1)
    tis = xml.rindex('<TrackItems', 0, am.start()); tie = xml.index('</TrackItems>', am.start())
    region = xml[tis:tie].rstrip() + f'\n\t\t\t<TrackItem Index="0" {refattr}="{idmap[template_cap["vct"]]}"/>' + '\n\t\t'
    idx = [0]
    def renum(m):
        s = f'<TrackItem Index="{idx[0]}" {m.group(1)}="{m.group(2)}"/>'; idx[0] += 1; return s
    region = re.sub(r'<TrackItem Index="\d+" (ObjectU?Ref)="([^"]+)"/>', renum, region)
    return xml[:tis] + region + xml[tie:], idmap

def max_object_id(xml):
    return max(int(m) for m in re.findall(r'ObjectID="(\d+)"', xml))
