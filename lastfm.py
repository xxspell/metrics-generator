import traceback
from datetime import datetime
from typing import Dict, List, Any


def parse_tracks(data: Dict[str, Any]) -> Dict[str, Any]:
    tracks_data = data.get("recenttracks", {})
    tracks_list = tracks_data.get("track", [])
    attrs = tracks_data.get("@attr", {})

    tracks: List[Dict[str, str]] = []
    for i, track in enumerate(tracks_list, start=1):
        title = track.get("name", "")
        artist = track.get("artist", {}).get("name", "") if isinstance(track.get("artist"), dict) else ""
        url = track.get("url", "")

        tracks.append({
            "number": str(i),
            "title": title,
            "artist": artist,
            "track_url": url
        })

    total_scrobbles = int(attrs.get("total", 0))
    last_scrobble_date = None
    if tracks_list:
        last_scrobble_date = tracks_list[0].get("date", {}).get("#text")

    return {
        "tracks": tracks,
        "summary": {
            "total_scrobbles": total_scrobbles,
            "last_scrobble_date": last_scrobble_date
        }
    }


def escape_xml(text):
    return (text.replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;'))

def generate_lastfm_svg(scrobbles, start_x=390, start_y=120, max_line_length=60):
    svg_parts = []

    header_y = start_y
    svg_parts.append(
        f'<text x="{start_x}" y="{header_y}" fill="#c9d1d9" id="lastfm_block">')
    svg_parts.append(
        f'<tspan x="{start_x}" y="{header_y}">- Last.fm Recent Scrobbles</tspan> -—————————————————————————————-—-')
    current_y = header_y + 20

    min_dots_count = 1
    tracks = scrobbles.get("tracks", [])
    for track in tracks:
        number = track['number']
        title = track['title']
        artist = track['artist']

        prefix = f"{number}."
        prefix_len = len(f". {prefix}")

        value_text = f"{title} - {artist}"

        value_len = len(value_text)

        available_dots = max_line_length - prefix_len - value_len - 1

        if available_dots >= min_dots_count:
            dots_count = available_dots
        else:
            space_for_value = max_line_length - prefix_len - 1 - min_dots_count
            if value_len > space_for_value:
                value_text = value_text[:space_for_value - 3] + '...'
            dots_count = min_dots_count

        split_pos = value_text.rfind(' - ')
        if split_pos != -1:
            title_part_text = value_text[:split_pos + 3]
            artist_part_text = value_text[split_pos + 3:]
        else:
            title_part_text = value_text
            artist_part_text = ''

        dots = '.' * max(0, dots_count)

        line = f'<tspan x="{start_x}" y="{current_y}" class="cc">. </tspan><tspan class="key">{prefix}</tspan>'
        line += f'<tspan class="cc">{dots} </tspan><tspan class="value">{escape_xml(title_part_text)}</tspan><tspan class="artistColor">{escape_xml(artist_part_text)}</tspan>'

        svg_parts.append(line)
        current_y += 20

    summary = scrobbles.get("summary", {})
    total_scrobbles = summary.get("total_scrobbles", 0)
    last_scrobble_date = summary.get("last_scrobble_date")
    if total_scrobbles > 0 and last_scrobble_date:
        try:
            last_date = datetime.strptime(last_scrobble_date, '%d %b %Y, %H:%M')
            now = datetime.now()
            delta = now - last_date

            if delta.days >= 1:
                days = delta.days
                ago = f"{days} day{'s' if days != 1 else ''} ago"
            elif delta.seconds >= 3600:
                hours = delta.seconds // 3600
                ago = f"{hours} hour{'s' if hours != 1 else ''} ago"
            elif delta.seconds >= 60:
                minutes = delta.seconds // 60
                ago = f"{minutes} minute{'s' if minutes != 1 else ''} ago"
            else:
                ago = "just now"
        except ValueError:
            traceback.print_exc()
            ago = "unknown"

        number_str = str(total_scrobbles)
        len_number = len(number_str)
        dots1_count = 15 - len_number
        if dots1_count < 0:
            dots1_count = 0
        dots1 = '.' * dots1_count

        len_ago = len(ago)
        dots2_count = 15 - len_ago
        if dots2_count < 0:
            dots2_count = 0
            if len_ago > 15:
                ago = ago[:12] + '...'
        dots2 = '.' * dots2_count

        line = f'<tspan x="{start_x}" y="{current_y}" class="cc">. </tspan><tspan class="key">Total: </tspan><tspan class="cc">{dots1} </tspan><tspan class="value">{escape_xml(number_str)}</tspan><tspan class="value"> scrobbles</tspan> | <tspan class="key">Last: </tspan><tspan class="cc">{dots2} </tspan>'
        if ago in ["just now", "unknown"]:
            line += f'<tspan class="value">{escape_xml(ago)}</tspan>'
        else:
            parts = ago.split(' ', 1)
            ago_number = parts[0]
            ago_rest = parts[1]
            line += f'<tspan class="value">{escape_xml(ago_number)}</tspan> <tspan class="value">{escape_xml(ago_rest)}</tspan>'

        svg_parts.append(line)
        current_y += 20

    svg_parts.append('</text>')

    return '\n'.join(svg_parts)

async def parse_lastfm(session, api_key, user):
    url = f'http://ws.audioscrobbler.com/2.0/?method=user.getrecenttracks&user={user}&api_key={api_key}&format=json&limit=5&extended=1'
    async with session.get(url) as response:
        if response.status == 200:
            return await response.json()
        raise Exception(response.status, await response.text())

async def lastfm_getter(session, api_key, user):
    data = await parse_lastfm(session, api_key, user)
    scrobbles = parse_tracks(data)
    return generate_lastfm_svg(scrobbles)
