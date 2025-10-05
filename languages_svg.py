import asyncio

LANGUAGE_COLORS = {
    'Python': '#3572A5',
    'JavaScript': '#c9bb4d',
    'TypeScript': '#2b7489',
    'Java': '#ed8c33',
    'C++': '#f34b7d',
    'C': '#555555',
    'Go': '#00ADD8',
    'Rust': '#dea584',
    'PHP': '#4F5D95',
    'Ruby': '#701516',
    'HTML': '#e34c26',
    'CSS': '#1572B6',
    'Shell': '#89e051',
    'Swift': '#fa7343',
    'Kotlin': '#A97BFF',
    'Dart': '#00B4AB',
    'Scala': '#c22d40',
    'R': '#198CE7',
    'MATLAB': '#e16737',
    'Lua': '#000080',
    'Perl': '#0298c3',
    'Haskell': '#5e5086',
    'Clojure': '#db5855',
    'Elixir': '#6e4a7e',
    'Erlang': '#B83998',
    'Julia': '#a270ba',
    'Racket': '#3c5caa',
    'Scheme': '#1e4aec',
    'Assembly': '#6E4C13',
    'Verilog': '#b2b7f8',
    'VHDL': '#adb2cb',
    'TeX': '#3D6117',
    'Makefile': '#427819',
    'Dockerfile': '#384d54',
    'YAML': '#cb171e',
    'JSON': '#292929',
    'XML': '#0060ac',
    'Markdown': '#083fa1',
    'Text': '#cccccc',  # Default for unknown
}

def human_readable_size(size_in_bytes):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if size_in_bytes < 1024:
            return f"{size_in_bytes:.1f}{unit}"
        size_in_bytes /= 1024
    return f"{size_in_bytes:.1f}PB"


async def get_most_used_languages(session, user_name, headers, excluded_repos=None, owner_affiliation=['OWNER'], excluded_languages=None):
    if excluded_repos is None:
        excluded_repos = []
    if excluded_languages is None:
        excluded_languages = []
    else:
        excluded_languages = [lang.lower() for lang in excluded_languages]

    if excluded_repos is None:
        excluded_repos = []

    repos = []
    cursor = None
    while True:
        query = '''
        query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
            user(login: $login) {
                repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                    edges {
                        node {
                            ... on Repository {
                                nameWithOwner
                            }
                        }
                    }
                    pageInfo {
                        endCursor
                        hasNextPage
                    }
                }
            }
        }'''
        variables = {'owner_affiliation': owner_affiliation, 'login': user_name, 'cursor': cursor}
        async with session.post('https://api.github.com/graphql', json={'query': query, 'variables': variables}, headers=headers) as response:
            if response.status != 200:
                raise Exception(f'Failed to fetch repos: {response.status}')
            data = await response.json()
            repos.extend([edge['node']['nameWithOwner'] for edge in data['data']['user']['repositories']['edges']])
            if not data['data']['user']['repositories']['pageInfo']['hasNextPage']:
                break
            cursor = data['data']['user']['repositories']['pageInfo']['endCursor']

    repos = [repo for repo in repos if repo not in excluded_repos]

    language_bytes = {}
    for repo in repos:
        owner, name = repo.split('/')
        async with session.get(f'https://api.github.com/repos/{owner}/{name}/languages', headers=headers) as response:
            print(f"{owner}/{name} {await response.json()}")
            if response.status == 200:
                langs = await response.json()
                for lang, bytes_count in langs.items():
                    language_bytes[lang] = language_bytes.get(lang, 0) + bytes_count

    sorted_langs = sorted(language_bytes.items(), key=lambda x: x[1], reverse=True)

    filtered_langs = [(lang, bytes_count) for lang, bytes_count in sorted_langs if lang.lower() not in excluded_languages]
    top_langs = filtered_langs[:5]

    total_bytes = sum(bytes for _, bytes in top_langs)

    languages = []
    for lang, bytes_count in top_langs:
        percentage = (bytes_count / total_bytes) * 100 if total_bytes > 0 else 0
        color = LANGUAGE_COLORS.get(lang, '#cccccc')
        languages.append({
            'name': lang,
            'color': color,
            'percentage': percentage,
            'bytes_count': bytes_count
        })

    return languages

def generate_languages_svg(x, y, fill_color, languages):
    svg = f'''<text x="{x}" y="{y}" fill="{fill_color}" id="languages_block">
    <tspan x="{x}" y="{y}">- Most used languages</tspan> -—————————————————————————————-—-'''

    current_y = y + 20
    max_line_length = 59
    for i, lang in enumerate(languages[:5], 1):
        name = lang['name']
        color = lang['color']
        percentage = lang['percentage']
        bytes_count = lang.get('bytes_count', 0)

        percentage_str = f"{percentage:.1f}%"
        size_str = f"({human_readable_size(bytes_count)})"
        full_value_str = f"{percentage_str} {size_str}"

        content_length = len(str(i)) + 1 + len(name) + 1 + len(full_value_str) + 2
        dots_length = max(0, max_line_length - content_length)
        dots = '.' * dots_length
        svg += f'''
    <tspan x="{x}" y="{current_y}" class="cc">. </tspan><tspan class="key">{i}.</tspan><tspan class="cc">{dots} </tspan><tspan fill="{color}">{name}</tspan> <tspan class="value">{percentage_str}</tspan><tspan fill="{color}" class="artistColor"> {size_str}</tspan>'''
        current_y += 20

    bar_y = current_y + 10
    bar_x = x
    total_symbols = 56
    svg += f'\n<tspan x="{bar_x}" y="{bar_y}" font-family="monospace">   </tspan>'
    for lang in languages[:5]:
        percentage = lang['percentage']
        num_symbols = round(percentage / 100 * total_symbols)
        color = lang['color']
        symbol = '▓'
        svg += f'<tspan fill="{color}" font-family="monospace">{symbol * num_symbols}</tspan>'
    svg += f'<tspan font-family="monospace">   </tspan>'

    svg += '\n</text>'

    return svg
