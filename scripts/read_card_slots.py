import pymem
import struct

def read_slots():
    try:
        pm = pymem.Pymem('torchlight_infinite.exe')
    except Exception as e:
        print('Could not attach to game:', e)
        return
        
    with open(r'c:\Users\Daniel\Documents\GitHub\tli-v3\moje\[torchlight_infinite] Objects Dump_UI_cardselection.txt', 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
    
    slots = []
    for line in lines:
        if 'CanvasPanelSlot' in line and 'UIMysticMapItem' in line and '[UObject:' in line:
            parts = line.split('[UObject:')
            if len(parts) > 1:
                addr_str = parts[1].split(']')[0]
                addr = int(addr_str, 16)
                path = parts[1].split('CanvasPanelSlot  ')[1].strip() if 'CanvasPanelSlot  ' in parts[1] else line.strip()
                slots.append((addr, path))

    print(f'Found {len(slots)} CanvasPanelSlots related to UIMysticMapItem')
    
    results = []
    for addr, path in slots:
        try:
            left = pm.read_float(addr + 0x38)
            top = pm.read_float(addr + 0x3C)
            results.append((addr, left, top, path))
        except Exception as e:
            pass
            
    # filter for those with reasonable coordinates (say not 0, 0 exactly for all?) Actually 0,0 is fine, but let's just print top 50 or sort by X, Y
    # Looking for the map elements, maybe they have specific names
    for addr, left, top, path in results:
        # Just map items, maybe filter out inner elements of the card itself if they are slots?
        # The card itself might be a child of a canvas panel, so the path might be like '...WidgetTree.CanvasPanel_1.CanvasPanelSlot_5'
        print(f'Addr: {hex(addr)} | Left: {left:.2f}, Top: {top:.2f} | Path: {path}')

if __name__ == '__main__':
    read_slots()
