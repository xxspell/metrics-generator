from svg_header import make_header_tail


def justify_text(new_text, length=0, use_k=False):
    if isinstance(new_text, int):
        candidates = [f"{'{:,}'.format(new_text)}"]
        if use_k:
            scales = [
                (1_000_000_000_000, 't'),
                (1_000_000_000, 'b'),
                (1_000_000, 'm'),
                (1_000, 'k'),
            ]
            for divider, suffix in scales:
                if new_text >= divider:
                    value = new_text / divider
                    candidates.append(f"{value:.1f}{suffix}")
                    candidates.append(f"{value:.2f}{suffix}")
        for cand in candidates:
            if len(cand) <= length:
                new_text = cand
                break
        else:
            new_text = min(candidates, key=len)
    new_text = str(new_text)
    just_len = max(0, length - len(new_text))
    if just_len <= 2:
        dot_map = {0: ' ', 1: ' ', 2: '. '}
        dot_string = dot_map[just_len]
    else:
        dot_string = ' ' + ('.' * just_len) + ' '
    return dot_string, new_text


def generate_github_stats_svg(x, y, fill_color, commit_data, star_data, repo_data, contrib_data, follower_data, loc_total, loc_add, loc_del, recent_commit_data=None, streak_data=None):
    repo_dots, repo_val = justify_text(repo_data, 6)
    star_dots, star_val = justify_text(star_data, 14)
    commit_dots, commit_val = justify_text(commit_data, 23)
    follower_dots, follower_val = justify_text(follower_data, 10)
    loc_dots, loc_val = justify_text(loc_total, 9, True)
    loc_add_dots, loc_add_val = justify_text(loc_add, 0, True)
    loc_del_dots, loc_del_val = justify_text(loc_del, 7, True)
    contrib_dots, contrib_val = justify_text(contrib_data, 4)

    pipe_prefix = ' '
    svg = f'''<text x="{x}" y="{y}" fill="{fill_color}" id="github_stats">
    <tspan x="{x}" y="{y}">- GitHub Stats</tspan>{make_header_tail('- GitHub Stats')}
    <tspan x="{x}" y="{y+20}" class="cc">. </tspan><tspan class="key">Repos</tspan>:<tspan class="cc" id="repo_data_dots">{repo_dots}</tspan><tspan class="value" id="repo_data">{repo_val}</tspan> {{<tspan class="key">Contributed</tspan>:<tspan class="cc" id="contrib_data_dots">{contrib_dots}</tspan><tspan class="value" id="contrib_data">{contrib_val}</tspan>}}{pipe_prefix}| <tspan class="key">Stars</tspan>:<tspan class="cc" id="star_data_dots">{star_dots}</tspan><tspan class="value" id="star_data">{star_val}</tspan>
    <tspan x="{x}" y="{y+40}" class="cc">. </tspan><tspan class="key">Commits</tspan>:<tspan class="cc" id="commit_data_dots">{commit_dots}</tspan><tspan class="value" id="commit_data">{commit_val}</tspan> | <tspan class="key">Followers</tspan>:<tspan class="cc" id="follower_data_dots">{follower_dots}</tspan><tspan class="value" id="follower_data">{follower_val}</tspan>'''

    if recent_commit_data is not None or streak_data is not None:
        if recent_commit_data is not None:
            recent_dots, recent_val = justify_text(recent_commit_data, 18)
            recent_part = f'<tspan class="key">Commits (7d)</tspan>:<tspan class="cc" id="recent_commit_dots">{recent_dots}</tspan><tspan class="value" id="recent_commit_data">{recent_val}</tspan>'
        else:
            recent_part = ''

        if streak_data is not None:
            streak_dots, streak_val = justify_text(streak_data, 8)
            streak_part = f' | <tspan class="key">Streak</tspan>:<tspan class="cc" id="streak_dots">{streak_dots}</tspan><tspan class="value" id="streak_data">{streak_val} days</tspan>'
        else:
            streak_part = ''

        combined_part = recent_part + streak_part
        svg += f'\n    <tspan x="{x}" y="{y+60}" class="cc">. </tspan>{combined_part}'

    y_pos_base = y + 80 if (recent_commit_data is not None or streak_data is not None) else y + 60

    loc_total_int = int(str(loc_total).replace(',', ''))
    loc_add_int = int(str(loc_add).replace(',', ''))
    loc_del_int = int(str(loc_del).replace(',', ''))

    full_total = f"{loc_total_int:,}"
    full_add = f"{loc_add_int:,}"
    full_del = f"{loc_del_int:,}"

    k_total = f"{loc_total_int // 1000:,}k"
    k_add = f"{loc_add_int // 1000:,}k"
    k_del = f"{loc_del_int // 1000:,}k"

    loc_label_plain = '. Lines of Code on GitHub:'
    row2_plain = f". Commits:{commit_dots}{commit_val} | Followers:{follower_dots}{follower_val}"
    limit = len(row2_plain)

    candidates = []
    variants = [
        (full_total, full_add, full_del, 0),
        (k_total, full_add, full_del, 1),
        (full_total, k_add, full_del, 1),
        (full_total, full_add, k_del, 1),
        (k_total, k_add, full_del, 2),
        (k_total, full_add, k_del, 2),
        (full_total, k_add, k_del, 2),
        (k_total, k_add, k_del, 3),
    ]

    for total_candidate, add_candidate, del_candidate, compact_count in variants:
        plain = f"{loc_label_plain} {total_candidate} ( {add_candidate}++, {del_candidate}-- )"
        candidates.append((compact_count, len(plain), total_candidate, add_candidate, del_candidate))

    fitting = [c for c in candidates if c[1] <= limit]
    if fitting:
        def fit_key(c):
            compact_count, plain_len, total_candidate, add_candidate, del_candidate = c
            total_is_compact = total_candidate.endswith('k')
            add_is_compact = add_candidate.endswith('k')
            del_is_compact = del_candidate.endswith('k')
            return (compact_count, -plain_len, total_is_compact, add_is_compact, del_is_compact)

        fitting.sort(key=fit_key)
        _, _, loc_val, loc_add_val, loc_del_val = fitting[0]
    else:
        candidates.sort(key=lambda c: (c[1], c[0]))
        _, _, loc_val, loc_add_val, loc_del_val = candidates[0]

    loc_dots = ' '
    loc_del_dots = ''

    svg += f'''
    <tspan x="{x}" y="{y_pos_base}" class="cc">. </tspan><tspan class="key">Lines of Code on GitHub</tspan>:<tspan class="cc" id="loc_data_dots">{loc_dots}</tspan><tspan class="value" id="loc_data">{loc_val}</tspan> ( <tspan class="addColor" id="loc_add">{loc_add_val}</tspan><tspan class="addColor">++</tspan>, <tspan id="loc_del_dots">{loc_del_dots}</tspan><tspan class="delColor" id="loc_del">{loc_del_val}</tspan><tspan class="delColor">--</tspan> )
</text>'''

    return svg