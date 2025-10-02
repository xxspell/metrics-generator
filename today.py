
import asyncio
import datetime
import hashlib
import os
import time

import aiohttp
from dateutil import relativedelta
from lxml import etree

from art import load_ascii_from_file, ascii_to_svg, get_random_file
from lastfm import lastfm_getter

# Fine-grained personal access token with All Repositories access:
# Account permissions: read:Followers, read:Starring, read:Watching
# Repository permissions: read:Commit statuses, read:Contents, read:Issues, read:Metadata, read:Pull Requests
# Issues and pull requests permissions not needed at the moment, but may be used in the future
HEADERS = {'authorization': 'token '+ os.environ['ACCESS_TOKEN']}
PROXY = os.environ.get('PROXY')
USER_NAME = os.environ['USER_NAME']
EXCLUDED_REPOS = os.environ.get('EXCLUDED_REPOS', '').split(',') if os.environ.get('EXCLUDED_REPOS') else []
LASTFM_TOKEN = os.environ.get('LASTFM_TOKEN')
LASTFM_USER = os.environ.get('LASTFM_USER')
QUERY_COUNT = {'user_getter': 0, 'follower_getter': 0, 'graph_repos_stars': 0, 'recursive_loc': 0, 'graph_commits': 0, 'loc_query': 0}


def daily_readme(birthday):
    """
    Returns the length of time since I was born
    e.g. 'XX years, XX months, XX days'
    """
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    return '{} {}, {} {}, {} {}{}'.format(
        diff.years, 'year' + format_plural(diff.years),
        diff.months, 'month' + format_plural(diff.months),
        diff.days, 'day' + format_plural(diff.days),
        ' 🎂' if (diff.months == 0 and diff.days == 0) else '')


def format_plural(unit):
    """
    Returns a properly formatted number
    e.g.
    'day' + format_plural(diff.days) == 5
    >>> '5 days'
    'day' + format_plural(diff.days) == 1
    >>> '1 day'
    """
    return 's' if unit != 1 else ''


async def simple_request(session, func_name, query, variables):
    """
    Returns a request, or raises an Exception if the response does not succeed.
    """
    async with session.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers=HEADERS) as response:
        if response.status == 200:
            return await response.json()
        raise Exception(func_name, ' has failed with a', response.status, await response.text(), QUERY_COUNT)


async def graph_commits(session, start_date, end_date):
    """
    Uses GitHub's GraphQL v4 API to return my total commit count
    """
    query_count('graph_commits')
    query = '''
    query($start_date: DateTime!, $end_date: DateTime!, $login: String!) {
        user(login: $login) {
            contributionsCollection(from: $start_date, to: $end_date) {
                contributionCalendar {
                    totalContributions
                }
            }
        }
    }'''
    variables = {'start_date': start_date,'end_date': end_date, 'login': USER_NAME}
    request = await simple_request(session, graph_commits.__name__, query, variables)
    return int(request['data']['user']['contributionsCollection']['contributionCalendar']['totalContributions'])


async def graph_repos_stars(session, count_type, owner_affiliation, cursor=None, add_loc=0, del_loc=0):
    """
    Uses GitHub's GraphQL v4 API to return my total repository, star, or lines of code count.
    """
    query_count('graph_repos_stars')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: $owner_affiliation) {
                totalCount
                edges {
                    node {
                        ... on Repository {
                            nameWithOwner
                            stargazers {
                                totalCount
                            }
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
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = await simple_request(session, graph_repos_stars.__name__, query, variables)
    if request['data']['user']['repositories']['pageInfo']['hasNextPage']:
        return await graph_repos_stars(session, count_type, owner_affiliation, request['data']['user']['repositories']['pageInfo']['endCursor'], add_loc, del_loc)
    if count_type == 'repos':
        return request['data']['user']['repositories']['totalCount']
    elif count_type == 'stars':
        return stars_counter(request['data']['user']['repositories']['edges'])


async def recursive_loc(session, owner, repo_name, data, cache_comment, addition_total=0, deletion_total=0, my_commits=0, cursor=None):
    """
    Uses GitHub's GraphQL v4 API and cursor pagination to fetch 100 commits from a repository at a time
    """
    query_count('recursive_loc')
    query = '''
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            totalCount
                            edges {
                                node {
                                    ... on Commit {
                                        committedDate
                                    }
                                    author {
                                        user {
                                            id
                                        }
                                    }
                                    deletions
                                    additions
                                }
                            }
                            pageInfo {
                                endCursor
                                hasNextPage
                            }
                        }
                    }
                }
            }
        }
    }'''
    variables = {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}
    async with session.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers=HEADERS) as response:
        # print(response.status)
        if response.status == 200:
            json_data = await response.json()
            if json_data['data']['repository']['defaultBranchRef'] != None: # Only count commits if repo isn't empty
                return await loc_counter_one_repo(session, owner, repo_name, data, cache_comment, json_data['data']['repository']['defaultBranchRef']['target']['history'], addition_total, deletion_total, my_commits)
            else: return 0
        await force_close_file(data, cache_comment) # saves what is currently in the file before this program crashes
        if response.status == 403:
            raise Exception('Too many requests in a short amount of time!\nYou\'ve hit the non-documented anti-abuse limit!')
        raise Exception('recursive_loc() has failed with a', response.status, await response.text(), QUERY_COUNT)


async def loc_counter_one_repo(session, owner, repo_name, data, cache_comment, history, addition_total, deletion_total, my_commits):
    """
    Recursively call recursive_loc (since GraphQL can only search 100 commits at a time)
    only adds the LOC value of commits authored by me
    """
    # print(repo_name)
    for node in history['edges']:
        if node['node']['author']['user'] == OWNER_ID:
            my_commits += 1
            addition_total += node['node']['additions']
            deletion_total += node['node']['deletions']

    if history['edges'] == [] or not history['pageInfo']['hasNextPage']:
        return addition_total, deletion_total, my_commits
    else: return await recursive_loc(session, owner, repo_name, data, cache_comment, addition_total, deletion_total, my_commits, history['pageInfo']['endCursor'])


async def loc_query(session, owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=[]):
    """
    Uses GitHub's GraphQL v4 API to query all the repositories I have access to (with respect to owner_affiliation)
    Queries 60 repos at a time, because larger queries give a 502 timeout error and smaller queries send too many
    requests and also give a 502 error.
    Returns the total number of lines of code in all repositories
    """
    query_count('loc_query')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
            edges {
                node {
                    ... on Repository {
                        nameWithOwner
                        defaultBranchRef {
                            target {
                                ... on Commit {
                                    history {
                                        totalCount
                                        }
                                    }
                                }
                            }
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
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = await simple_request(session, loc_query.__name__, query, variables)
    if request['data']['user']['repositories']['pageInfo']['hasNextPage']:   # If repository data has another page
        edges += request['data']['user']['repositories']['edges']            # Add on to the LoC count
        return await loc_query(session, owner_affiliation, comment_size, force_cache, request['data']['user']['repositories']['pageInfo']['endCursor'], edges)
    else:
        return await cache_builder(session, edges + request['data']['user']['repositories']['edges'], comment_size, force_cache)


async def cache_builder(session, edges, comment_size, force_cache, loc_add=0, loc_del=0):
    """
    Checks each repository in edges to see if it has been updated since the last time it was cached
    If it has, run recursive_loc on that repository to update the LOC count
    Excludes repositories specified in EXCLUDED_REPOS environment variable
    """
    # Filter out excluded repositories
    excluded_repos = get_excluded_repos()
    if excluded_repos:
        filtered_edges = []
        for edge in edges:
            repo_name = edge['node']['nameWithOwner']
            if repo_name not in excluded_repos:
                filtered_edges.append(edge)
        edges = filtered_edges
        # print(edges)
        print(f"Filtered out {len(excluded_repos)} excluded repositories: {', '.join(excluded_repos)}")

    cached = True # Assume all repositories are cached
    filename = 'cache/'+hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest()+'.txt' # Create a unique filename for each user
    try:
        with open(filename, 'r') as f:
            data = f.readlines()
    except FileNotFoundError: # If the cache file doesn't exist, create it
        data = []
        if comment_size > 0:
            for _ in range(comment_size): data.append('This line is a comment block. Write whatever you want here.\n')
        with open(filename, 'w') as f:
            f.writelines(data)

    if len(data)-comment_size != len(edges) or force_cache: # If the number of repos has changed, or force_cache is True
        cached = False
        flush_cache(edges, filename, comment_size)
        with open(filename, 'r') as f:
            data = f.readlines()

    cache_comment = data[:comment_size] # save the comment block
    data = data[comment_size:] # remove those lines
    for index in range(len(edges)):
        repo_hash, commit_count, *__ = data[index].split()
        if repo_hash == hashlib.sha256(edges[index]['node']['nameWithOwner'].encode('utf-8')).hexdigest():
            try:
                if int(commit_count) != edges[index]['node']['defaultBranchRef']['target']['history']['totalCount']:
                    # if commit count has changed, update loc for that repo
                    owner, repo_name = edges[index]['node']['nameWithOwner'].split('/')

                    loc = await recursive_loc(session, owner, repo_name, data, cache_comment)
                    data[index] = repo_hash + ' ' + str(edges[index]['node']['defaultBranchRef']['target']['history']['totalCount']) + ' ' + str(loc[2]) + ' ' + str(loc[0]) + ' ' + str(loc[1]) + '\n'
            except TypeError: # If the repo is empty
                data[index] = repo_hash + ' 0 0 0 0\n'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    for line in data:
        loc = line.split()
        loc_add += int(loc[3])
        loc_del += int(loc[4])
    return [loc_add, loc_del, loc_add - loc_del, cached]


def flush_cache(edges, filename, comment_size):
    """
    Wipes the cache file
    This is called when the number of repositories changes or when the file is first created
    """
    with open(filename, 'r') as f:
        data = []
        if comment_size > 0:
            data = f.readlines()[:comment_size] # only save the comment
    with open(filename, 'w') as f:
        f.writelines(data)
        for node in edges:
            f.write(hashlib.sha256(node['node']['nameWithOwner'].encode('utf-8')).hexdigest() + ' 0 0 0 0\n')


def add_archive():
    """
    Several repositories I have contributed to have since been deleted.
    This function adds them using their last known data
    """
    with open('cache/repository_archive.txt', 'r') as f:
        data = f.readlines()
    old_data = data
    data = data[7:len(data)-3] # remove the comment block
    added_loc, deleted_loc, added_commits = 0, 0, 0
    contributed_repos = len(data)
    for line in data:
        repo_hash, total_commits, my_commits, *loc = line.split()
        added_loc += int(loc[0])
        deleted_loc += int(loc[1])
        if (my_commits.isdigit()): added_commits += int(my_commits)
    added_commits += int(old_data[-1].split()[4][:-1])
    return [added_loc, deleted_loc, added_loc - deleted_loc, added_commits, contributed_repos]


async def force_close_file(data, cache_comment):
    """
    Forces the file to close, preserving whatever data was written to it
    This is needed because if this function is called, the program would've crashed before the file is properly saved and closed
    """
    filename = 'cache/'+hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest()+'.txt'
    with open(filename, 'w') as f:
        f.writelines(cache_comment)
        f.writelines(data)
    print('There was an error while writing to the cache file. The file,', filename, 'has had the partial data saved and closed.')


def stars_counter(data):
    """
    Count total stars in repositories owned by me
    """
    total_stars = 0
    for node in data: total_stars += node['node']['stargazers']['totalCount']
    return total_stars


def svg_overwrite(filename, age_data, commit_data, star_data, repo_data, contrib_data, follower_data, loc_data, lastfm_svg, ascii_svg):
    """
    Parse SVG files and update elements with my age, commits, stars, repositories, and lines written
    """
    tree = etree.parse(filename)
    root = tree.getroot()
    justify_format(root, 'commit_data', commit_data, 22)
    justify_format(root, 'star_data', star_data, 14)
    justify_format(root, 'repo_data', repo_data, 6)
    justify_format(root, 'contrib_data', contrib_data)
    justify_format(root, 'follower_data', follower_data, 10)
    justify_format(root, 'loc_data', loc_data[2], 9)
    justify_format(root, 'loc_add', loc_data[0])
    justify_format(root, 'loc_del', loc_data[1], 7)


    def overwrite_blocks(svg_block, name):
        new_text_element = etree.fromstring(svg_block)
        old_text_element = root.find(f".//*[@id='{name}']")

        if old_text_element is not None:
            parent = old_text_element.getparent()
            parent.replace(old_text_element, new_text_element)
        else:
            print(f"Not found {name} class")

    overwrite_blocks(lastfm_svg,"lastfm_block")
    overwrite_blocks(ascii_svg,"ascii")

    def replace_colors(tree, mapping: dict):
        for old, new in mapping.items():
            for el in tree.xpath(f'//*[@fill="{old}"]'):
                el.attrib["fill"] = new

    if "dark" in filename:
        replace_colors(tree, {
            "#24292f": "#c9d1d9"})
    elif "light" in filename:
        replace_colors(tree, {
            "#c9d1d9": "#24292f"})

    tree.write(filename, encoding='utf-8', xml_declaration=True, )


def justify_format(root, element_id, new_text, length=0):
    """
    Updates and formats the text of the element, and modifes the amount of dots in the previous element to justify the new text on the svg
    """
    if isinstance(new_text, int):
        new_text = f"{'{:,}'.format(new_text)}"
    new_text = str(new_text)
    find_and_replace(root, element_id, new_text)
    just_len = max(0, length - len(new_text))
    if just_len <= 2:
        dot_map = {0: '', 1: ' ', 2: '. '}
        dot_string = dot_map[just_len]
    else:
        dot_string = ' ' + ('.' * just_len) + ' '
    find_and_replace(root, f"{element_id}_dots", dot_string)


def find_and_replace(root, element_id, new_text):
    """
    Finds the element in the SVG file and replaces its text with a new value
    """
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        element.text = new_text


def commit_counter(comment_size):
    """
    Counts up my total commits, using the cache file created by cache_builder.
    """
    total_commits = 0
    filename = 'cache/'+hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest()+'.txt' # Use the same filename as cache_builder
    with open(filename, 'r') as f:
        data = f.readlines()
    cache_comment = data[:comment_size] # save the comment block
    data = data[comment_size:] # remove those lines
    for line in data:
        total_commits += int(line.split()[2])
    return total_commits


async def user_getter(session, username):
    """
    Returns the account ID and creation time of the user
    """
    query_count('user_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            id
            createdAt
        }
    }'''
    variables = {'login': username}
    request = await simple_request(session, user_getter.__name__, query, variables)
    return {'id': request['data']['user']['id']}, request['data']['user']['createdAt']

async def follower_getter(session, username):
    """
    Returns the number of followers of the user
    """
    query_count('follower_getter')
    query = '''
    query($login: String!){
        user(login: $login) {
            followers {
                totalCount
            }
        }
    }'''
    request = await simple_request(session, follower_getter.__name__, query, {'login': username})
    return int(request['data']['user']['followers']['totalCount'])


def query_count(funct_id):
    """
    Counts how many times the GitHub GraphQL API is called
    """
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


def get_excluded_repos():
    """
    Returns a cleaned list of excluded repositories from environment variable
    """
    if not EXCLUDED_REPOS:
        return []

    # Clean up repository names (strip whitespace, filter empty strings)
    cleaned_repos = []
    for repo in EXCLUDED_REPOS:
        repo = repo.strip()
        if repo:
            cleaned_repos.append(repo)

    return cleaned_repos


async def perf_counter(funct, *args):
    """
    Calculates the time it takes for a function to run
    Returns the function result and the time differential
    """
    import inspect
    start = time.perf_counter()
    if inspect.iscoroutinefunction(funct):
        funct_return = await funct(*args)
    else:
        funct_return = funct(*args)
    return funct_return, time.perf_counter() - start


def formatter(query_type, difference, funct_return=False, whitespace=0):
    """
    Prints a formatted time differential
    Returns formatted result if whitespace is specified, otherwise returns raw result
    """
    print('{:<23}'.format('   ' + query_type + ':'), sep='', end='')
    print('{:>12}'.format('%.4f' % difference + ' s ')) if difference > 1 else print('{:>12}'.format('%.4f' % (difference * 1000) + ' ms'))
    if whitespace:
        return f"{'{:,}'.format(funct_return): <{whitespace}}"
    return funct_return


async def main():
    print('Calculation times:')
    async with aiohttp.ClientSession(proxy=PROXY) as session:
        user_data, user_time = await perf_counter(user_getter, session, USER_NAME)
        global OWNER_ID
        OWNER_ID, acc_date = user_data
        formatter('account data', user_time)
        age_data, age_time = await perf_counter(daily_readme, datetime.datetime(2002, 7, 5))
        formatter('age calculation', age_time)
        total_loc, loc_time = await perf_counter(loc_query, session, ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'], 7)
        formatter('LOC (cached)', loc_time) if total_loc[-1] else formatter('LOC (no cache)', loc_time)
        commit_data, commit_time = await perf_counter(commit_counter, 7)
        formatter('commit calculation', commit_time)
        star_data, star_time = await perf_counter(graph_repos_stars, session, 'stars', ['OWNER'])
        formatter('star calculation', star_time)
        repo_data, repo_time = await perf_counter(graph_repos_stars, session, 'repos', ['OWNER'])
        formatter('repo calculation', repo_time)
        contrib_data, contrib_time = await perf_counter(graph_repos_stars, session, 'repos', ['OWNER', 'COLLABORATOR', 'ORGANIZATION_MEMBER'])
        formatter('contri calculation', contrib_time)
        follower_data, follower_time = await perf_counter(follower_getter, session, USER_NAME)
        formatter('follower calculation', follower_time)
        if OWNER_ID == {'id': 'MDQ6VXNlcjc0OTcyMzk'}:
            archived_data = add_archive()
            for index in range(len(total_loc)-1):
                total_loc[index] += archived_data[index]
            contrib_data += archived_data[-1]
            commit_data += int(archived_data[-2])

        for index in range(len(total_loc)-1): total_loc[index] = '{:,}'.format(total_loc[index]) # format added, deleted, and total LOC

        lastfm_svg, lastfm_time = await perf_counter(lastfm_getter, session, LASTFM_TOKEN, LASTFM_USER)
        formatter('lastfm calculation', lastfm_time)

        def ascii_getter():
            return ascii_to_svg(load_ascii_from_file(get_random_file('arts/')), 15, 30, "#c9d1d9")

        ascii_svg, ascii_time = await perf_counter(ascii_getter)
        formatter('ascii calculation', lastfm_time)


        svg_overwrite('dark_mode.svg', age_data, commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1], lastfm_svg, ascii_svg)
        svg_overwrite('light_mode.svg', age_data, commit_data, star_data, repo_data, contrib_data, follower_data, total_loc[:-1], lastfm_svg, ascii_svg)

        # move cursor to override 'Calculation times:' with 'Total function time:' and the total function time, then move cursor back
        print(
            '\x1b[13F',
            '{:<21}'.format('Total function time:'),
            '{:>11}'.format('%.4f' % (
                        user_time + age_time + loc_time + commit_time + star_time + repo_time + contrib_time + lastfm_time + ascii_time)),
            ' s ' + '\x1b[E' * 13,
            sep=''
        )

        print('Total GitHub GraphQL API calls:', '{:>3}'.format(sum(QUERY_COUNT.values())))
        for funct_name, count in QUERY_COUNT.items(): print('{:<28}'.format('   ' + funct_name + ':'), '{:>6}'.format(count))




if __name__ == '__main__':
    asyncio.run(main())

