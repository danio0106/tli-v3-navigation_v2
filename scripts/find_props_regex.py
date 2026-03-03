import os
import re

file_path = r'c:\Users\Daniel\Documents\GitHub\tli-v3\moje\[torchlight_infinite] Objects Dump_UI_cardselection.txt'

target_keywords = ['slot', 'rendertransform', 'index', 'mapid', 'position', 'id', 'canvas', 'widgettree']

def search_props(class_name):
    print(f"=== Properties for {class_name} ===")
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            if class_name in line and '[Offset:' in line:
                # To filter out instance specific names, check if it's actually the property definition
                if '/Script/' in line or 'Game/BluePrint/' in line:
                    lower_line = line.lower()
                    if any(kw in lower_line for kw in target_keywords):
                        # Clean up line to just show Offset and Property name
                        match = re.search(r'\[Offset:(.*?)\]\s+\(Size:(.*?)\).*?(ObjectProperty|StructProperty|IntProperty|FloatProperty|Class|DelegateProperty|TextProperty|ArrayProperty)\s+(.*?)\s+', line)
                        if match:
                            offset = match.group(1)
                            size = match.group(2)
                            prop_type = match.group(3)
                            prop_name = match.group(4).split(':')[-1]
                            
                            # Only print if prop name contains one of the keywords
                            if any(kw in prop_name.lower() for kw in target_keywords):
                                print(f"Offset: {offset} | Size: {size} | Type: {prop_type} | Name: {prop_name}")
                        else:
                            # fallback if regex fails
                            if any(kw in line.lower() for kw in target_keywords):
                                words = line.split()
                                offset_part = [w for w in words if w.startswith('[Offset:')]
                                if offset_part:
                                    print(line.strip())

search_props('UIMysticMapItem_C')
search_props('UUI_BasicBase')
search_props('UUserWidget')
search_props('UWidget')

