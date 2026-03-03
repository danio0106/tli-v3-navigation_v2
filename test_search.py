
with open(r'c:\Users\Daniel\Documents\GitHub\tli-v3\moje\[torchlight_infinite] Objects Dump_UI_cardselection.txt', mode='r', encoding='utf-8', errors='ignore') as f:
    lines = f.readlines()

def print_class(cls_name):
    found = False
    super_cls = None
    for i, line in enumerate(lines):
        if line.startswith(f'class {cls_name}'):
            print(f'=== {line.strip()} ===')
            found = True
            if ':' in line:
                super_cls = line.split(':')[-1].strip()
            
            for j in range(i+1, len(lines)):
                if lines[j].strip() == '' or lines[j].startswith('class '):
                    break
                
                prop = lines[j].strip()
                lower_prop = prop.lower()
                if 'slot' in lower_prop or 'transform' in lower_prop or 'index' in lower_prop or 'mapid' in lower_prop or 'position' in lower_prop or 'id' in lower_prop:
                    print(prop)
            break
    if super_cls:
        print_class(super_cls)

print_class('UIMysticMapItem_C')

