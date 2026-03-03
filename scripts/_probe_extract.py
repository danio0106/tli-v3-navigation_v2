import json, sys

with open(r'c:\Users\Daniel\Documents\GitHub\tli-v3\data\card_probe_20260302_163247.json', 'r') as f:
    data = json.load(f)

widgets = data['widgets']
print(f'Total widgets: {len(widgets)}')
print()

for i, w in enumerate(widgets):
    has_map = bool(w.get('map_item_subs', {}))
    has_card = bool(w.get('card_item_subs', {}))
    card_subs = w.get('card_item_subs', {})

    if has_card:
        empty_bg = card_subs.get('EmptyBg', {})
        empty_icon = card_subs.get('EmptyIcon', {})
        card_icon = card_subs.get('CardIconMask', {})
        eff_sw = card_subs.get('EffectSwitcher', {})
        buff_icon = card_subs.get('BuffIcon', {})
        frame_img = card_subs.get('FrameImg', {})

        eb_vis = empty_bg.get('visibility', 'N/A')
        ei_vis = empty_icon.get('visibility', 'N/A')
        tex = card_icon.get('icon_tex_name', 'N/A')
        eidx = eff_sw.get('active_index', 'N/A')
        brf = buff_icon.get('brush_res_fname', 'N/A')
        fr_vis = frame_img.get('visibility', 'N/A')
        fr_brf = frame_img.get('brush_res_fname', 'N/A')

        card_present = "CARD PRESENT" if eb_vis == 1 else ("EMPTY SLOT" if eb_vis == 4 else f"UNKNOWN(vis={eb_vis})")

        print(f'W{i}: map_subs={"YES" if has_map else "NO"}, card_subs=YES ({len(card_subs)} keys) => {card_present}')
        print(f'  EmptyBg.vis={eb_vis}, EmptyIcon.vis={ei_vis}')
        print(f'  CardIconMask.icon_tex_name={tex}')
        print(f'  EffectSwitcher.active_index={eidx}')
        print(f'  BuffIcon.brush_res_fname={brf}')
        print(f'  FrameImg.vis={fr_vis}, brush_res_fname={fr_brf}')

        # Also list ALL sub-widget names and types
        print(f'  All card sub-widgets: {", ".join(card_subs.keys())}')
    else:
        print(f'W{i}: map_subs={"YES" if has_map else "NO"}, card_subs=EMPTY')
    print()

# Summary table
print("=" * 100)
print("SUMMARY TABLE")
print("=" * 100)
print(f'{"Widget":<8} {"map_subs":<10} {"card_subs":<10} {"EmptyBg":<10} {"CardIcon":<30} {"EffSw":<6} {"BuffIcon":<30}')
print("-" * 100)
for i, w in enumerate(widgets):
    has_map = "YES" if bool(w.get('map_item_subs', {})) else "NO"
    has_card = bool(w.get('card_item_subs', {}))
    if has_card:
        cs = w['card_item_subs']
        eb = cs.get('EmptyBg', {}).get('visibility', '?')
        tex = cs.get('CardIconMask', {}).get('icon_tex_name', '?')
        eidx = cs.get('EffectSwitcher', {}).get('active_index', '?')
        brf = cs.get('BuffIcon', {}).get('brush_res_fname', '?')
        # Shorten names
        tex_short = tex.replace('UI_SpCard_Main_', '') if tex != '?' else '?'
        brf_short = brf.replace('UI_SpCard_Mark_', '') if brf != '?' else '?'
        print(f'W{i:<7} {has_map:<10} {"YES":<10} {str(eb):<10} {tex_short:<30} {str(eidx):<6} {brf_short:<30}')
    else:
        print(f'W{i:<7} {has_map:<10} {"NO":<10} {"-":<10} {"-":<30} {"-":<6} {"-":<30}')
