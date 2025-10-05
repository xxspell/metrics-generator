def justify_text(new_text, length=0, use_k=False):
    if isinstance(new_text, int):
        if use_k and new_text >= 1000:
            new_text = f"{new_text // 1000}k"
        else:
            new_text = f"{'{:,}'.format(new_text)}"
    new_text = str(new_text)
    just_len = max(0, length - len(new_text))
    if just_len <= 2:
        dot_map = {0: '', 1: ' ', 2: '. '}
        dot_string = dot_map[just_len]
    else:
        dot_string = ' ' + ('.' * just_len) + ' '
    return dot_string, new_text


def generate_github_stats_svg(x, y, fill_color, commit_data, star_data, repo_data, contrib_data, follower_data, loc_total, loc_add, loc_del):
    repo_dots, repo_val = justify_text(repo_data, 7)
    star_dots, star_val = justify_text(star_data, 14)
    commit_dots, commit_val = justify_text(commit_data, 23)
    follower_dots, follower_val = justify_text(follower_data, 10)
    loc_dots, loc_val = justify_text(loc_total, 9, True)
    loc_add_dots, loc_add_val = justify_text(loc_add, 0, True)
    loc_del_dots, loc_del_val = justify_text(loc_del, 7, True)
    contrib_dots, contrib_val = justify_text(contrib_data, 0)

    svg = f'''<text x="{x}" y="{y}" fill="{fill_color}" id="github_stats">
    <tspan x="{x}" y="{y}">- GitHub Stats</tspan> -—————————————————————————————————————————-—-
    <tspan x="{x}" y="{y+20}" class="cc">. </tspan><tspan class="key">Repos</tspan>:<tspan class="cc" id="repo_data_dots">{repo_dots}</tspan><tspan class="value" id="repo_data">{repo_val}</tspan> {{<tspan class="key">Contributed</tspan>: <tspan class="value" id="contrib_data">{contrib_val}</tspan>}} | <tspan class="key">Stars</tspan>:<tspan class="cc" id="star_data_dots">{star_dots}</tspan><tspan class="value" id="star_data">{star_val}</tspan>
    <tspan x="{x}" y="{y+40}" class="cc">. </tspan><tspan class="key">Commits</tspan>:<tspan class="cc" id="commit_data_dots">{commit_dots}</tspan><tspan class="value" id="commit_data">{commit_val}</tspan> | <tspan class="key">Followers</tspan>:<tspan class="cc" id="follower_data_dots">{follower_dots}</tspan><tspan class="value" id="follower_data">{follower_val}</tspan>
    <tspan x="{x}" y="{y+60}" class="cc">. </tspan><tspan class="key">Lines of Code on GitHub</tspan>:<tspan class="cc" id="loc_data_dots">{loc_dots}</tspan><tspan class="value" id="loc_data">{loc_val}</tspan> ( <tspan class="addColor" id="loc_add">{loc_add_val}</tspan><tspan class="addColor">++</tspan>, <tspan id="loc_del_dots">{loc_del_dots}</tspan><tspan class="delColor" id="loc_del">{loc_del_val}</tspan><tspan class="delColor">--</tspan> )
</text>'''

    return svg