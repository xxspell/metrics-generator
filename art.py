import os
import random


def ascii_to_svg(ascii_art, x, y, fill_color):
    lines = ascii_art.split('\n')
    lines = [line.rstrip() for line in lines]

    # Calculate dimensions

    max_line_length = max(len(line) for line in lines)
    # width = max_line_length * y
    # height = len(lines) * y
    # Start SVG
    svg_parts = [f'''
  <text x="{x}" y="{y}" fill="{fill_color}" class="ascii"> ''']

    # Add each line
    for i, line in enumerate(lines):

        y_pos = y + (i * 20)

        # Escape XML characters
        escaped_line = line.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        svg_parts.append(f'    <tspan x="{x}" y="{y_pos}">{escaped_line}</tspan>')

    svg_parts.append('  </text>\n')
    return '\n'.join(svg_parts)

def get_random_file(folder_path: str) -> str:
    files = [f for f in os.listdir(folder_path) if os.path.isfile(os.path.join(folder_path, f))]
    if not files:
        raise FileNotFoundError("There are no files in the folder")
    return os.path.join(folder_path, random.choice(files))

def load_ascii_from_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        return f.read()


