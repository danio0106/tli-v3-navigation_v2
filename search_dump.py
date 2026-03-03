import sys

dump_file = r'c:\Users\Daniel\Documents\GitHub\tli-v3\moje\[torchlight_infinite] Objects Dump_UI_cardselection.txt'
keywords = ['CanvasPanelSlot', 'PanelSlot', 'MysteryCardItem_C', 'WidgetTransform', 'Position', 'LayoutData', 'Offsets']

def main():
    try:
        with open(dump_file, 'r', encoding='utf-8', errors='ignore') as f:
            current_class = ""
            class_lines = []
            capture = False
            for line in f:
                stripped_line = line.strip()
                if line.startswith('class ') or line.startswith('struct '):
                    if capture and class_lines:
                        print(f"\n--- {current_class} ---")
                        for cl in class_lines:
                            print(cl)
                    current_class = stripped_line
                    class_lines = []
                    # Check if class/struct name itself implies we should capture its properties
                    capture = any(kw.lower() in current_class.lower() for kw in keywords)
                elif current_class and '[Offset:' in line:
                    lower_line = line.lower()
                    # Capture if this line has a keyword
                    if any(kw.lower() in lower_line for kw in keywords):
                        capture = True
                    if capture:
                        class_lines.append(stripped_line)
            
            # Print last captured block
            if capture and class_lines:
                print(f"\n--- {current_class} ---")
                for cl in class_lines:
                    print(cl)
    except Exception as e:
        print('Error:', e)

if __name__ == "__main__":
    main()