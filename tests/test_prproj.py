"""Smoke test for prproj_lib: blob codec, then full surgery cycle on a fabricated
mini-project that mirrors the documented caption structure."""
import sys, os, re, gzip, base64, struct
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '_scratch')
os.makedirs(OUT, exist_ok=True)
os.chdir(OUT)
import prproj_lib as P

ok = lambda name: print(f'  PASS  {name}')

# ---- 1. text blob codec -----------------------------------------------------
for text in ['Original text', 'Kur bejuokausi?', "a R*tardation?", 'F****** HAMMOND!']:
    blob = P.make_blob(text)
    assert struct.unpack('<Q', blob[:8])[0] == len(blob) - 12
    assert P.read_blob_text(blob) == text, text
    body, t2 = P.harvest_body(blob)
    assert body == P.STYLE_BODY and t2 == text
ok('blob round-trip (make -> read -> harvest, incl. ?, *, !)')

assert len(P.STYLE_BODY) == 752 and P.STYLE_BODY[:4] == bytes.fromhex('44332211')
assert b'Verdana-Bold' in P.STYLE_BODY[490:510]
ok('style body: 752 bytes, FlatBuffers magic, Verdana-Bold @492')

h = P.fab_hash(P.make_blob('x'))
assert re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', h)
ok('fab_hash emits md5-as-GUID')

# ---- 2. gzip save / save_like ----------------------------------------------
P.save('<A><B>x</B></A>', 'ref.prproj')
assert gzip.open('ref.prproj').read() == b'<A><B>x</B></A>'
# save_like mirrors a REAL .prproj header (10 bytes, no FNAME flag) — emulate that
open('ref10.prproj', 'wb').write(gzip.compress(b'<A><B>x</B></A>'))
P.save_like(open('ref10.prproj','rb').read(), b'<A><B>y</B></A>', 'forged.prproj')
assert gzip.open('forged.prproj').read() == b'<A><B>y</B></A>'
assert open('forged.prproj','rb').read()[:10] == open('ref10.prproj','rb').read()[:10]
ok('save + save_like (forged header round-trips through gzip)')

# ---- 3. fabricated mini-project ---------------------------------------------
Z = 914457600000000            # graphics zero: 3600s @ 25fps
blob0 = base64.b64encode(P.make_blob('Original text')).decode()
params = [f'''<ArbVideoComponentParam ObjectID="200" ClassID="c-arb" Version="1">
 <StartKeyframeValue Encoding="base64" BinaryHash="{P.fab_hash(P.make_blob('Original text'))}">{blob0}</StartKeyframeValue>
</ArbVideoComponentParam>''']
for i in range(1, 22):
    params.append(f'''<VideoComponentParam ObjectID="{200+i}" ClassID="c-vcp" Version="1">
 <StartKeyframe>-91445760000000000,0,0,0,0,0,0,0</StartKeyframe>
 <CurrentValue>0</CurrentValue>
</VideoComponentParam>''')
param_refs = '\n  '.join(f'<Param Index="{i}" ObjectRef="{200+i}"/>' for i in range(22))
MINI = f'''<PremiereData Version="3">
<VideoTrack ObjectID="10" ClassID="c-track" Version="1">
 <TrackItems Version="1">
  <TrackItem Index="0" ObjectRef="100"/>
 </TrackItems>
</VideoTrack>
<VideoClipTrackItem ObjectID="100" ClassID="c-vcti" Version="1">
 <ClipTrackItem Version="1">
  <TrackItem Version="1">
   <Start>{Z}</Start>
   <End>{Z + 2*P.TICKS}</End>
  </TrackItem>
  <SubClip ObjectRef="110"/>
  <ComponentOwner Version="1">
   <Components ObjectRef="120"/>
  </ComponentOwner>
 </ClipTrackItem>
</VideoClipTrackItem>
<SubClip ObjectID="110" ClassID="c-sub" Version="1">
 <Clip ObjectRef="111"/>
</SubClip>
<VideoClip ObjectID="111" ClassID="c-vc" Version="1">
 <Clip Version="1">
  <InPoint>{Z}</InPoint>
  <OutPoint>{Z + 2*P.TICKS}</OutPoint>
 </Clip>
</VideoClip>
<ComponentChain ObjectID="120" ClassID="c-chain" Version="1">
 <Components Version="1">
  <Component Index="0" ObjectRef="130"/>
 </Components>
</ComponentChain>
<VideoFilterComponent ObjectID="130" ClassID="c-vfc" Version="1">
 <Component Version="1">
  <MatchName>AE.ADBE Text</MatchName>
  <Params Version="1">
  {param_refs}
  </Params>
 </Component>
</VideoFilterComponent>
{chr(10).join(params)}
</PremiereData>'''
P.save(MINI, 'mini.prproj')

xml, root, by_id, by_uid, deref = P.load('mini.prproj')
caps = P.find_captions(root, deref)
assert len(caps) == 1 and caps[0]['text'] == 'Original text' and caps[0]['ncomps'] == 1
assert caps[0]['st'] == Z and caps[0]['inp'] == Z and len(caps[0]['pids']) == 22
ok('load + find_captions on fabricated project (text, times, 22 params)')

# set_text + retime + apply_style, then verify through a full save/reload
xml = P.set_text(xml, caps[0], 'Kur bejuokausi?')
xml = P.retime(xml, caps[0], 3601.0, 3602.5)     # sequence 1.0s..2.5s past graphics zero
donor_m = open(os.path.join(ROOT, 'assets/')+'donor_motion.xml').read()
donor_v = open(os.path.join(ROOT, 'assets/')+'donor_vm.xml').read()
nid = [P.max_object_id(xml) + 1]
xml = P.apply_style(xml, caps[0], None, donor_m, donor_v, nid)
P.save(xml, 'styled.prproj')

xml2, root2, *_ , deref2 = P.load('styled.prproj')
caps2 = P.find_captions(root2, deref2)
c = caps2[0]
assert len(caps2) == 1 and c['text'] == 'Kur bejuokausi?'
assert c['st'] == int(3601.0*P.TICKS) and c['en'] == int(3602.5*P.TICKS)
assert c['outp'] == c['inp'] + (c['en'] - c['st'])
assert c['ncomps'] == 3, f"chain not rewired: {c['ncomps']} comps"
assert 'AE.ADBE Motion' in xml2 and 'AE.ADBE Graphic Group' in xml2
ok('set_text + retime survive save/reload')
ok('apply_style: Motion + VM cloned in, chain rewired to Motion/VM/Text')

import pickle
dp = pickle.load(open(os.path.join(ROOT, 'assets/')+'donor_params.pkl','rb'))
rebased0, rebased1 = str(c['inp']), str(c['inp'] + (dp['pop_t1'] - dp['pop_t0']))
m = re.search(r'<Keyframes>([^<]*47\.952[^<]*)</Keyframes>', xml2)
assert m and rebased0 in m.group(1) and rebased1 in m.group(1)
assert str(dp['pop_t0']) not in xml2
ok('pop keyframes rebased to clip InPoint (media time), donor ticks gone')

# clone + verify, then delete-by-merge sanity on the numbers
nid = [P.max_object_id(xml2) + 1]
xml3, idmap = P.clone_caption(xml2, c, nid)
P.save(xml3, 'cloned.prproj')
_, root3, *_ , deref3 = P.load('cloned.prproj')
caps3 = P.find_captions(root3, deref3)
assert len(caps3) == 2 and all(x['text'] == 'Kur bejuokausi?' and x['ncomps'] == 3 for x in caps3)
assert sorted(int(m.group(1)) for m in re.finditer(r'<TrackItem Index="(\d+)" ObjectRef=', xml3)) == [0, 1]
new_ids = {x['vct'] for x in caps3}
assert len(new_ids) == 2
ok('clone_caption: full closure clone, fresh IDs, TrackItems renumbered')

P.set_text(xml3, caps3[1], 'Antras tekstas')  # clone independently editable
ok('cloned caption is independently editable')

print('\nprproj_lib: ALL PASS')
