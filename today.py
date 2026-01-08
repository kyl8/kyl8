import datetime
from dateutil import relativedelta
import requests
import os
from lxml import etree
import time
import hashlib
import subprocess
import json
import tempfile
import shutil


BIRTHDAY = datetime.datetime(2004, 10, 29)
try:
    from dotenv import load_dotenv
    load_dotenv() 
except ImportError:
    pass 

if 'ACCESS_TOKEN' not in os.environ:
    print("ERRO CRÍTICO: 'ACCESS_TOKEN' não encontrado.")
    exit(1)

HEADERS = {'authorization': 'token '+ os.environ['ACCESS_TOKEN']}
USER_NAME = os.environ['USER_NAME']
QUERY_COUNT = {'user_getter': 0, 'graph_repos_stars': 0, 'recursive_loc': 0, 'graph_commits': 0, 'loc_query': 0}


def daily_readme(birthday):
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    return '{}Y {}M {}D'.format(diff.years, diff.months, diff.days)


def format_uptime(birthday):
    diff = relativedelta.relativedelta(datetime.datetime.today(), birthday)
    parts = []
    if diff.years > 0:
        parts.append(f"{diff.years} year{'' if diff.years == 1 else 's'}")
    if diff.months > 0:
        parts.append(f"{diff.months} month{'' if diff.months == 1 else 's'}")
    if diff.days > 0:
        parts.append(f"{diff.days} day{'' if diff.days == 1 else 's'}")
    
    return ', '.join(parts) if parts else "0 days"


def format_plural(unit):
    return 's' if unit != 1 else ''


def simple_request(func_name, query, variables):
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers=HEADERS)
    if request.status_code == 200:
        return request
    raise Exception(func_name, ' has failed', request.status_code)


def graph_commits(start_date, end_date):
    query_count('graph_commits')
    query = '''
    query($start_date: DateTime!, $end_date: DateTime!, $login: String!) {
        user(login: $login) {
            contributionsCollection(from: $start_date, to: $end_date) {
                contributionCalendar { totalContributions }
            }
        }
    }'''
    variables = {'start_date': start_date,'end_date': end_date, 'login': USER_NAME}
    try:
        request = simple_request(graph_commits.__name__, query, variables)
        data = request.json()
        
        if 'errors' in data:
            print(f"GraphQL Error: {data['errors']}")
            return 0
        
        contrib = data['data']['user']['contributionsCollection']
        if contrib is None:
            print("contributionsCollection is None, trying alternative query")
            return 0
        
        return int(contrib['contributionCalendar']['totalContributions'])
    except Exception as e:
        print(f"Error in graph_commits: {e}")
        return 0


def get_total_commits():
    query = '''
    query ($login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: [OWNER]) {
                edges {
                    node {
                        name
                        defaultBranchRef {
                            target {
                                ... on Commit {
                                    history(first: 100) {
                                        totalCount
                                    }
                                }
                            }
                        }
                    }
                }
                pageInfo { endCursor hasNextPage }
            }
        }
    }'''
    
    total_commits = 0
    cursor = None
    
    try:
        while True:
            variables = {'login': USER_NAME, 'cursor': cursor}
            request = simple_request('get_total_commits', query, variables)
            data = request.json()
            
            if 'errors' in data:
                print(f"GraphQL errors in get_total_commits: {data['errors']}")
                break
            
            repos = data['data']['user']['repositories']
            
            for edge in repos['edges']:
                if edge['node']['defaultBranchRef'] and edge['node']['defaultBranchRef']['target']:
                    history = edge['node']['defaultBranchRef']['target'].get('history', {})
                    if history:
                        total_commits += history.get('totalCount', 0)
            
            if not repos['pageInfo']['hasNextPage']:
                break
            
            cursor = repos['pageInfo']['endCursor']
        
        print(f"DEBUG: Total commits in own repos: {total_commits}")
        return total_commits
    except Exception as e:
        print(f"Error calculating commits: {e}")
        return 0


def get_total_loc():
    ignore_langs = ['JSON', 'YAML', 'XML', 'Markdown', 'HTML', 'CSS', 'SCSS', 'Sass', 
                    'TOML', 'INI', 'Properties', 'Dockerfile', 'Makefile']
    
    query = '''
    query ($login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: [OWNER]) {
                edges {
                    node {
                        name
                        isFork
                        url
                        defaultBranchRef {
                            name
                        }
                    }
                }
                pageInfo { endCursor hasNextPage }
            }
        }
    }'''
    
    total_loc = 0
    cursor = None
    
    try:
        while True:
            variables = {'login': USER_NAME, 'cursor': cursor}
            request = simple_request('get_total_loc', query, variables)
            data = request.json()
            
            if 'errors' in data:
                print(f"GraphQL errors in get_total_loc: {data['errors']}")
                break
            
            repos = data['data']['user']['repositories']
            
            for edge in repos['edges']:
                repo_name = edge['node']['name']
                is_fork = edge['node'].get('isFork', False)
                repo_url = edge['node']['url']
                default_branch = edge['node'].get('defaultBranchRef', {}).get('name', 'main')
                if repo_name == 'kyl8' or is_fork:
                    continue
                loc_data = count_loc_with_cloc(repo_url, default_branch, ignore_langs)
                total_loc += loc_data
                if loc_data > 0:
                    print(f"  -> {repo_name}: {loc_data} LOC")
            
            if not repos['pageInfo']['hasNextPage']:
                break
            
            cursor = repos['pageInfo']['endCursor']
        
        print(f"DEBUG: Total LOC (cloc, excluding config files) in own repos: {total_loc}")
        return total_loc
    except Exception as e:
        print(f"Error calculating LOC: {e}")
        return 0


def count_loc_with_cloc(repo_url, branch, ignore_langs):
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        repo_path = os.path.join(temp_dir, 'repo')
        
        auth_url = repo_url.replace('https://', f'https://{os.environ.get("ACCESS_TOKEN")}@')
        result = subprocess.run(
            ['git', 'clone', '--branch', branch, '--single-branch', auth_url, repo_path],
            capture_output=True, timeout=30, text=True
        )
        
        if result.returncode != 0:
            return 0
        
        ignore_str = ','.join(ignore_langs)
        result = subprocess.run(
            ['cloc', repo_path, '--json', '--exclude-lang=' + ignore_str],
            capture_output=True, timeout=60, text=True
        )
        
        if result.returncode == 0:
            try:
                cloc_data = json.loads(result.stdout)
                return cloc_data.get('SUM', {}).get('code', 0)
            except json.JSONDecodeError:
                return 0
        
        return 0
    except subprocess.TimeoutExpired:
        print(f"    cloc timeout")
        return 0
    except Exception as e:
        print(f"    cloc error: {e}")
        return 0
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


def get_top_languages(limit=5):
    query = '''
    query ($login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor, ownerAffiliations: [OWNER]) {
                edges {
                    node {
                        name
                        languages(first: 10) {
                            edges {
                                node {
                                    name
                                }
                                size
                            }
                        }
                    }
                }
                pageInfo { endCursor hasNextPage }
            }
        }
    }'''
    
    language_sizes = {}
    cursor = None
    
    try:
        while True:
            variables = {'login': USER_NAME, 'cursor': cursor}
            request = simple_request('get_top_languages', query, variables)
            data = request.json()
            
            if 'errors' in data:
                print(f"GraphQL errors in get_top_languages: {data['errors']}")
                break
            
            repos = data['data']['user']['repositories']
            print(f"DEBUG: get_top_languages - processando {len(repos['edges'])} repositórios")
            
            for edge in repos['edges']:
                repo_name = edge['node']['name']
                languages = edge['node'].get('languages', {}).get('edges', [])
                repo_langs = []
                for lang_edge in languages:
                    lang_name = lang_edge['node']['name']
                    size = lang_edge.get('size', 0)
                    language_sizes[lang_name] = language_sizes.get(lang_name, 0) + size
                    size_kb = size / 1024
                    repo_langs.append(f"{lang_name} ({size_kb:.1f} KB)")
                print(f"  -> {repo_name}: {', '.join(repo_langs) if repo_langs else 'No languages'}")
            
            if not repos['pageInfo']['hasNextPage']:
                break
            
            cursor = repos['pageInfo']['endCursor']
        
        language_loc = {}
        for lang_name, size_bytes in language_sizes.items():
            size_kb = size_bytes / 1024
            estimated_lines = int(size_kb * 250)
            language_loc[lang_name] = estimated_lines
        sorted_langs = sorted(language_loc.items(), key=lambda x: x[1], reverse=True)
        top_langs = sorted_langs[:limit]
        total_loc = sum(loc for _, loc in top_langs)
        lang_percentages = []
        
        for lang_name, loc in top_langs:
            percentage = (loc / total_loc * 100) if total_loc > 0 else 0
            lang_percentages.append((lang_name, int(percentage)))
        result = " | ".join([f"{name} {pct}%" for name, pct in lang_percentages])
        print(f"DEBUG: Top languages by LOC (total repos processed): {result}")
        sorted_langs_display = [(name, loc) for name, loc in sorted_langs]
        print(f"DEBUG: Language LOC: {sorted_langs_display}")
        return result
    except Exception as e:
        print(f"Error calculating top languages: {e}")
        return "Python | JavaScript | TypeScript"


def graph_repos_stars(count_type, cursor=None, total_stars=0, total_repos=0):
    query_count('graph_repos_stars')
    
    query = '''
    query ($login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 100, after: $cursor) {
                totalCount
                edges {
                    node {
                        name
                        stargazerCount
                    }
                }
                pageInfo { endCursor hasNextPage }
            }
        }
    }'''
    
    variables = {'login': USER_NAME, 'cursor': cursor}
    
    try:
        request = simple_request(graph_repos_stars.__name__, query, variables)
        data = request.json()
        if cursor is None:  
            print(f"DEBUG: User {USER_NAME} repositories response received")
        
        if 'errors' in data:
            print(f"DEBUG: GraphQL errors: {data['errors']}")
            return 0
        
        repos = data['data']['user']['repositories']
        
        if count_type == 'repos':
            return repos['totalCount']
        elif count_type == 'stars':
            for edge in repos['edges']:
                stars = edge['node']['stargazerCount']
                total_stars += stars
                if stars > 0:
                    print(f"DEBUG: {edge['node']['name']} has {stars} stars")
            if repos['pageInfo']['hasNextPage']:
                next_cursor = repos['pageInfo']['endCursor']
                return graph_repos_stars(count_type, next_cursor, total_stars, total_repos)
            
            print(f"DEBUG: Total stars found: {total_stars}")
            return total_stars
    except Exception as e:
        print(f"DEBUG: Error in graph_repos_stars: {e}")
        return 0
    
    return 0


def recursive_loc(owner, repo_name, data, cache_comment, addition_total=0, deletion_total=0, my_commits=0, cursor=None):
    query_count('recursive_loc')
    query = '''
    query ($repo_name: String!, $owner: String!, $cursor: String) {
        repository(name: $repo_name, owner: $owner) {
            defaultBranchRef {
                target {
                    ... on Commit {
                        history(first: 100, after: $cursor) {
                            edges {
                                node {
                                    author { user { id } }
                                    deletions additions
                                }
                            }
                            pageInfo { endCursor hasNextPage }
                        }
                    }
                }
            }
        }
    }'''
    variables = {'repo_name': repo_name, 'owner': owner, 'cursor': cursor}
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables':variables}, headers=HEADERS)
    if request.status_code == 200:
        target = request.json()['data']['repository']['defaultBranchRef']
        if target:
            return loc_counter_one_repo(owner, repo_name, data, cache_comment, target['target']['history'], addition_total, deletion_total, my_commits)
        return 0
    return 0


def loc_counter_one_repo(owner, repo_name, data, cache_comment, history, addition_total, deletion_total, my_commits):
    for node in history['edges']:
        if node['node']['author']['user'] and node['node']['author']['user']['id'] == OWNER_ID['id']:
            my_commits += 1
            addition_total += node['node']['additions']
            deletion_total += node['node']['deletions']
    if not history['pageInfo']['hasNextPage']:
        return addition_total, deletion_total, my_commits
    return recursive_loc(owner, repo_name, data, cache_comment, addition_total, deletion_total, my_commits, history['pageInfo']['endCursor'])


def loc_query(owner_affiliation, comment_size=0, force_cache=False, cursor=None, edges=[]):
    query_count('loc_query')
    query = '''
    query ($owner_affiliation: [RepositoryAffiliation], $login: String!, $cursor: String) {
        user(login: $login) {
            repositories(first: 60, after: $cursor, ownerAffiliations: $owner_affiliation) {
                edges {
                    node {
                        nameWithOwner
                        defaultBranchRef { target { ... on Commit { history { totalCount } } } }
                    }
                }
                pageInfo { endCursor hasNextPage }
            }
        }
    }'''
    variables = {'owner_affiliation': owner_affiliation, 'login': USER_NAME, 'cursor': cursor}
    request = simple_request(loc_query.__name__, query, variables)
    res = request.json()['data']['user']['repositories']
    if res['pageInfo']['hasNextPage']:
        return loc_query(owner_affiliation, comment_size, force_cache, res['pageInfo']['endCursor'], edges + res['edges'])
    return cache_builder(edges + res['edges'], comment_size, force_cache)


def cache_builder(edges, comment_size, force_cache):
    filename = 'cache/'+hashlib.sha256(USER_NAME.encode('utf-8')).hexdigest()+'.txt'
    total_add, total_del = 0, 0
    return [total_add, total_del, total_add - total_del, True]


def svg_overwrite(filename, age_data, commit_data, star_data, repo_data, loc_data, uptime_data, top_languages=''):
    if not os.path.exists(filename): return
    tree = etree.parse(filename)
    root = tree.getroot()
    
    find_and_replace(root, 'age_data', age_data)
    find_and_replace(root, 'commit_data', commit_data)
    find_and_replace(root, 'star_data', star_data)
    find_and_replace(root, 'repo_data', repo_data)
    find_and_replace(root, 'loc_data', loc_data)
    find_and_replace(root, 'uptime_data', uptime_data)
    find_and_replace(root, 'bio_data', USER_BIO)
    find_and_replace(root, 'prog_lang_data', PROG_LANGUAGES)
    find_and_replace(root, 'real_lang_data', SPOKEN_LANGUAGES)
    if top_languages:
        find_and_replace(root, 'languages_data', top_languages)

    tree.write(filename, encoding='utf-8', xml_declaration=True)


def find_and_replace(root, element_id, new_text):
    if isinstance(new_text, int): new_text = f"{new_text:,}"
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        tspans = element.findall('.//{http://www.w3.org/2000/svg}tspan')
        if tspans:
            tspans[0].text = str(new_text)
        else:
            element.text = str(new_text)


def user_getter(username):
    query = 'query($login: String!){ user(login: $login) { id createdAt } }'
    request = simple_request('user_getter', query, {'login': username})
    return {'id': request.json()['data']['user']['id']}, request.json()['data']['user']['createdAt']


def query_count(funct_id):
    global QUERY_COUNT
    QUERY_COUNT[funct_id] += 1


if __name__ == '__main__':
    user_data, _ = user_getter(USER_NAME)
    OWNER_ID = user_data
    
    age_str = daily_readme(BIRTHDAY)
    uptime_str = format_uptime(BIRTHDAY)
    stars = graph_repos_stars('stars')
    repos = graph_repos_stars('repos')
    commits = get_total_commits()
    loc_total = get_total_loc()
    top_langs = get_top_languages()

    print(f"DEBUG: Consultando usuário: {USER_NAME}")
    print(f"DEBUG: USER_ID: {OWNER_ID['id']}")
    
    svg_overwrite('dark_mode.svg', age_str, commits, stars, repos, loc_total, uptime_str, top_langs)
    svg_overwrite('light_mode.svg', age_str, commits, stars, repos, loc_total, uptime_str, top_langs)
    print("SVGs atualizados com sucesso.")