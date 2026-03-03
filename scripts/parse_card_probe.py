"""Parse card_probe JSON and extract key fields for analysis."""
import json

with open(r"c:\Users\Daniel\Documents\GitHub\tli-v3\data\card_probe_20260302_155538.json", "r") as f:
    data = json.load(f)

print(f"Widget count: {data['widget_count']}")
print()

for i, w in enumerate(data["widgets"]):
    print(f"=== Widget {i} @ {w['address']} vis={w['visibility']} ===")
    
    cs = w.get("card_item_subs", {})
    ms = w.get("map_item_subs", {})
    
    cs_empty = len(cs) == 0
    ms_empty = len(ms) == 0
    
    if cs_empty:
        print("  [card_item_subs EMPTY]")
    if ms_empty:
        print("  [map_item_subs EMPTY]")
    
    # EffectSwitcher
    es = cs.get("EffectSwitcher", {})
    print(f"  EffectSwitcher active_index: {es.get('active_index', 'N/A')}")
    
    # BuffIcon
    bi = cs.get("BuffIcon", {})
    print(f"  BuffIcon brush_res_fname: {bi.get('brush_res_fname', 'N/A')}  style_id: {bi.get('style_id', 'N/A')}")
    
    # CardIconMask
    cm = cs.get("CardIconMask", {})
    print(f"  CardIconMask icon_tex_name: {cm.get('icon_tex_name', 'N/A')}")
    
    # EmptyBg
    eb = cs.get("EmptyBg", {})
    print(f"  EmptyBg visibility: {eb.get('visibility', 'N/A')}")
    
    # EmptyIcon
    ei = cs.get("EmptyIcon", {})
    print(f"  EmptyIcon visibility: {ei.get('visibility', 'N/A')}")
    
    # Highlight
    hl = ms.get("Highlight", {})
    print(f"  Highlight visibility: {hl.get('visibility', 'N/A')}")
    
    # BossIcon
    boss = ms.get("BossIcon", {})
    print(f"  BossIcon brush_res_fname: {boss.get('brush_res_fname', 'N/A')}")
    
    has_card = eb.get("visibility") == 1 and ei.get("visibility") == 1
    print(f"  >>> HAS CARD: {has_card}")
    print()
