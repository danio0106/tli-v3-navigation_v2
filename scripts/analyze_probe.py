import json

with open(r'c:\Users\Daniel\Documents\GitHub\tli-v3\data\card_probe_20260302_154441.json', 'r') as f:
    data = json.load(f)

# Extract key fields for each widget
for i, w in enumerate(data['widgets']):
    addr = w.get("address", "?")
    vis = w.get("visibility", "?")
    opa = w.get("opacity", "?")
    print(f"=== WIDGET {i} (addr={addr}) ===")
    print(f"  top_visibility={vis}  opacity={opa}")

    # card_item_subs
    cs = w.get('card_item_subs', {})
    if cs:
        es = cs.get('EffectSwitcher', {})
        print(f"  EffectSwitcher active_index={es.get('active_index', 'N/A')}")

        fi = cs.get('FrameImg', {})
        print(f"  FrameImg style_id={fi.get('style_id', 'N/A')} brush_res_fname={fi.get('brush_res_fname', 'N/A')}")

        bi = cs.get('BuffIcon', {})
        print(f"  BuffIcon style_id={bi.get('style_id', 'N/A')} brush_res_fname={bi.get('brush_res_fname', 'N/A')}")

        cm = cs.get('CardIconMask', {})
        print(f"  CardIconMask icon_tex_name={cm.get('icon_tex_name', 'N/A')}")

        eb = cs.get('EmptyBg', {})
        print(f"  EmptyBg visibility={eb.get('visibility', 'N/A')}")

        ei = cs.get('EmptyIcon', {})
        print(f"  EmptyIcon visibility={ei.get('visibility', 'N/A')}")

        bib = cs.get('BuffIconBg', {})
        print(f"  BuffIconBg visibility={bib.get('visibility', 'N/A')}")
    else:
        print("  card_item_subs: EMPTY")

    # map_item_subs
    ms = w.get('map_item_subs', {})
    if ms:
        hl = ms.get('Highlight', {})
        print(f"  Highlight visibility={hl.get('visibility', 'N/A')}")

        gf = ms.get('GoldFrameBg', {})
        print(f"  GoldFrameBg visibility={gf.get('visibility', 'N/A')}")

        bt = ms.get('BossTalentPointSwitcher', {})
        print(f"  BossTalentPointSwitcher active_index={bt.get('active_index', 'N/A')}")

        boss = ms.get('BossIcon', {})
        print(f"  BossIcon brush_res_fname={boss.get('brush_res_fname', 'N/A')}")

        h2 = ms.get('hole2', {})
        print(f"  hole2 brush_res_fname={h2.get('brush_res_fname', 'N/A')}")

        h6 = ms.get('hole6', {})
        print(f"  hole6 brush_res_fname={h6.get('brush_res_fname', 'N/A')}")

        nm = ms.get('NormalMapNameBg', {})
        print(f"  NormalMapNameBg visibility={nm.get('visibility', 'N/A')}")
    else:
        print("  map_item_subs: EMPTY")
    print()

# Summary analysis
print("\n====== CROSS-WIDGET COMPARISON ======\n")

# Collect values
card_icon_masks = {}
buff_icons = {}
effect_switchers = {}
frame_style_ids = {}
empty_bg_vis = {}
empty_icon_vis = {}
highlight_vis = {}
gold_frame_vis = {}
frame_brush = {}

for i, w in enumerate(data['widgets']):
    cs = w.get('card_item_subs', {})
    ms = w.get('map_item_subs', {})

    if cs:
        cm = cs.get('CardIconMask', {})
        val = cm.get('icon_tex_name', '')
        card_icon_masks.setdefault(val, []).append(i)

        bi = cs.get('BuffIcon', {})
        val = bi.get('brush_res_fname', '')
        buff_icons.setdefault(val, []).append(i)

        es = cs.get('EffectSwitcher', {})
        val = es.get('active_index', -1)
        effect_switchers.setdefault(val, []).append(i)

        fi = cs.get('FrameImg', {})
        val = fi.get('style_id', -1)
        frame_style_ids.setdefault(val, []).append(i)

        val2 = fi.get('brush_res_fname', '')
        frame_brush.setdefault(val2, []).append(i)

        eb = cs.get('EmptyBg', {})
        val = eb.get('visibility', -1)
        empty_bg_vis.setdefault(val, []).append(i)

        ei = cs.get('EmptyIcon', {})
        val = ei.get('visibility', -1)
        empty_icon_vis.setdefault(val, []).append(i)

    if ms:
        hl = ms.get('Highlight', {})
        val = hl.get('visibility', -1)
        highlight_vis.setdefault(val, []).append(i)

        gf = ms.get('GoldFrameBg', {})
        val = gf.get('visibility', -1)
        gold_frame_vis.setdefault(val, []).append(i)

print("--- CardIconMask icon_tex_name groups ---")
for k, v in sorted(card_icon_masks.items(), key=lambda x: len(x[1]), reverse=True):
    print(f"  '{k}': widgets {v}")

print("\n--- BuffIcon brush_res_fname groups ---")
for k, v in sorted(buff_icons.items(), key=lambda x: len(x[1]), reverse=True):
    print(f"  '{k}': widgets {v}")

print("\n--- EffectSwitcher active_index groups ---")
for k, v in sorted(effect_switchers.items(), key=lambda x: len(x[1]), reverse=True):
    print(f"  {k}: widgets {v}")

print("\n--- FrameImg style_id groups ---")
for k, v in sorted(frame_style_ids.items(), key=lambda x: len(x[1]), reverse=True):
    print(f"  {k}: widgets {v}")

print("\n--- FrameImg brush_res_fname groups ---")
for k, v in sorted(frame_brush.items(), key=lambda x: len(x[1]), reverse=True):
    print(f"  '{k}': widgets {v}")

print("\n--- EmptyBg visibility groups ---")
for k, v in sorted(empty_bg_vis.items(), key=lambda x: len(x[1]), reverse=True):
    print(f"  {k}: widgets {v}")

print("\n--- EmptyIcon visibility groups ---")
for k, v in sorted(empty_icon_vis.items(), key=lambda x: len(x[1]), reverse=True):
    print(f"  {k}: widgets {v}")

print("\n--- Highlight visibility groups ---")
for k, v in sorted(highlight_vis.items(), key=lambda x: len(x[1]), reverse=True):
    print(f"  {k}: widgets {v}")

print("\n--- GoldFrameBg visibility groups ---")
for k, v in sorted(gold_frame_vis.items(), key=lambda x: len(x[1]), reverse=True):
    print(f"  {k}: widgets {v}")

# Also extract map names if available
print("\n====== MAP NAMES / BOSS INFO ======\n")
for i, w in enumerate(data['widgets']):
    ms = w.get('map_item_subs', {})
    boss = ms.get('BossIcon', {}).get('brush_res_fname', '')
    h2 = ms.get('hole2', {}).get('brush_res_fname', '')
    h6 = ms.get('hole6', {}).get('brush_res_fname', '')
    boss_sw = ms.get('BossTalentPointSwitcher', {}).get('active_index', -1)
    norm_vis = ms.get('NormalMapNameBg', {}).get('visibility', -1)
    print(f"  W{i}: BossIcon='{boss}' hole2='{h2}' hole6='{h6}' BossSw={boss_sw} NormNameBg_vis={norm_vis}")

# BuffIconBg visibility
print("\n--- BuffIconBg visibility groups ---")
buff_icon_bg_vis = {}
for i, w in enumerate(data['widgets']):
    cs = w.get('card_item_subs', {})
    if cs:
        bib = cs.get('BuffIconBg', {})
        val = bib.get('visibility', -1)
        buff_icon_bg_vis.setdefault(val, []).append(i)
for k, v in sorted(buff_icon_bg_vis.items(), key=lambda x: len(x[1]), reverse=True):
    print(f"  {k}: widgets {v}")
