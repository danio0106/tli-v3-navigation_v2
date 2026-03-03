import json

with open(r'c:\Users\Daniel\Documents\GitHub\tli-v3\data\card_probe_20260302_154441.json') as f:
    d = json.load(f)

# Check extra card_item_subs fields
for i in range(14):
    cs = d['widgets'][i].get('card_item_subs', {})
    lt = cs.get('LTIcon', {})
    rt = cs.get('RTIcon', {})
    tp = cs.get('TalentPointIcon', {})
    pp = cs.get('UIMysteryProgressItem', {})
    ui41 = cs.get('UIImage_41', {})
    print(f"W{i}: LTIcon vis={lt.get('visibility')} brush={lt.get('brush_res_fname','')}")
    print(f"      RTIcon vis={rt.get('visibility')} brush={rt.get('brush_res_fname','')}")
    print(f"      TalentPointIcon vis={tp.get('visibility')} brush={tp.get('brush_res_fname','')}")
    print(f"      UIMysteryProgressItem type={pp.get('type')} vis={pp.get('visibility')}")
    print(f"      UIImage_41 vis={ui41.get('visibility')} brush={ui41.get('brush_res_fname','')}")

# Check map_item_subs extra fields
print("\n--- map_item_subs extra ---")
for i in range(14):
    ms = d['widgets'][i].get('map_item_subs', {})
    ind = ms.get('Ind', {})
    mcv = ms.get('MysteryCardView', {})
    img71 = ms.get('UIImage_71', {})
    img74 = ms.get('UIImage_74', {})
    img280 = ms.get('UIImage_280', {})
    img644 = ms.get('UIImage_644', {})
    print(f"W{i}: Ind vis={ind.get('visibility')} | MysteryCardView vis={mcv.get('visibility')} type={mcv.get('type')}")
    print(f"      UIImage_71 brush={img71.get('brush_res_fname','')} | UIImage_74 brush={img74.get('brush_res_fname','')}")
    print(f"      UIImage_280 brush={img280.get('brush_res_fname','')} | UIImage_644 brush={img644.get('brush_res_fname','')}")
