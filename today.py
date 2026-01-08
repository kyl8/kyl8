#MODIFIED TODAY.PY CODE TO USE CLOC INSTEAD OF GITHUB'S API FOR LOC COUNTING AND TO EDIT MY PERSONAL SVG
#CODE CREDITS AND GITHUB API INTEGRATION GOES TO https://github.com/Andrew6rant/
import datetime
from dateutil import relativedelta
import requests
import os
from lxml import etree
import subprocess
import json
import tempfile
import shutil

print("DEBUG: SCRIPT VERSION = cloc-only FINAL")
BIRTHDAY = datetime.datetime(2004, 10, 29)

try:
    from dotenv import load_dotenv
    load_dotenv() 
except ImportError:
    pass 

IS_GITHUB_ACTIONS = os.environ.get('GITHUB_ACTIONS') == 'true'

if 'ACCESS_TOKEN' not in os.environ:
    if not IS_GITHUB_ACTIONS:
        print("AVISO: 'ACCESS_TOKEN' não encontrado. Use arquivo .env para desenvolvimento local.")
    else:
        print("ERRO CRÍTICO: 'ACCESS_TOKEN' não encontrado no GitHub Actions.")
        exit(1)

if 'USER_NAME' not in os.environ:
    if not IS_GITHUB_ACTIONS:
        print("AVISO: 'USER_NAME' não encontrado. Use arquivo .env para desenvolvimento local.")
    else:
        print("ERRO CRÍTICO: 'USER_NAME' não encontrado no GitHub Actions.")
        exit(1)

if not os.environ.get('ACCESS_TOKEN'):
    print("ERRO: 'ACCESS_TOKEN' não configurado.")
    exit(1)

if not os.environ.get('USER_NAME'):
    print("ERRO: 'USER_NAME' não configurado.")
    exit(1)

HEADERS = {'authorization': 'token '+ os.environ['ACCESS_TOKEN']}
USER_NAME = os.environ['USER_NAME']

REAL_LANGUAGES = {
    'Python', 'JavaScript', 'TypeScript', 'C++', 'C', 'Rust', 'Go', 'Java', 'C#',
    'Ruby', 'PHP', 'Swift', 'Kotlin', 'Objective-C', 'Objective-C++', 'Perl', 'Lua',
    'Haskell', 'Clojure', 'Scala', 'Groovy', 'R', 'MATLAB', 'Fortran', 'Cobol',
    'CUDA', 'Verilog', 'SystemVerilog', 'VHDL', 'Assembly', 'x86 Assembly',
    'Elixir', 'Erlang', 'Lisp', 'Scheme', 'Racket', 'Julia', 'Nim', 'Zig',
    'Crystal', 'D', 'Pascal', 'Delphi', 'Ada', 'Bash', 'Shell',
    'Bourne Shell', 'C Shell', 'Zsh', 'Fish Shell', 'PowerShell', 'Batch',
    'JSX', 'TSX', 'VB.NET', 'Visual Basic', 'F#', 'OCaml', 'SML'
}

LOC_CACHE = {}


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


def simple_request(func_name, query, variables):
    request = requests.post('https://api.github.com/graphql', json={'query': query, 'variables': variables}, headers=HEADERS)
    if request.status_code == 200:
        return request
    print(f"DEBUG: Request to {func_name} failed with status {request.status_code}")
    print(f"DEBUG: Response: {request.text[:200]}")
    raise Exception(func_name, ' has failed', request.status_code)


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
            
            user_data = data.get('data')
            if not user_data:
                break
            
            repos = user_data.get('user', {}).get('repositories', {})
            if not repos:
                break
            
            edges = repos.get('edges', [])
            
            for edge in edges:
                node = edge.get('node')
                if not node:
                    continue
                branch_ref = node.get('defaultBranchRef')
                if branch_ref:
                    target = branch_ref.get('target')
                    if target:
                        history = target.get('history', {})
                        if history:
                            total_commits += history.get('totalCount', 0)
            
            page_info = repos.get('pageInfo', {})
            if not page_info.get('hasNextPage', False):
                break
            
            cursor = page_info.get('endCursor')
        
        print(f"DEBUG: Total commits in own repos: {total_commits}")
        return total_commits
    except Exception as e:
        print(f"Error calculating commits: {e}")
        import traceback
        traceback.print_exc()
        return 0


def get_total_loc():
    """Get LOC by cloning all public repos locally using cloc"""
    total_loc = 0
    LOC_CACHE.clear()

    url = f'https://api.github.com/users/{USER_NAME}/repos'
    headers = {'Authorization': f'token {os.environ["ACCESS_TOKEN"]}'}
    params = {'type': 'owner', 'per_page': 100}
    page = 1

    while True:
        params['page'] = page
        response = requests.get(url, headers=headers, params=params)

        if response.status_code != 200:
            print(f"ERROR: Failed to fetch repos ({response.status_code})")
            break

        repos = response.json()
        if not repos:
            break

        for repo in repos:
            if repo is None or not isinstance(repo, dict):
                continue

            if repo.get('fork') or repo.get('private'):
                continue

            repo_name = repo.get('name')
            repo_url = repo.get('clone_url')
            branch = repo.get('default_branch', 'main')

            if not repo_name or not repo_url:
                continue

            print(f"DEBUG: Cloning {repo_name}")

            loc, langs = count_loc_with_cloc(repo_url, branch)
            if loc <= 0:
                continue

            LOC_CACHE[repo_name] = langs
            total_loc += loc

            langs_str = ", ".join(f"{l} ({c})" for l, c in langs)
            print(f"  -> {repo_name}: {loc} LOC [{langs_str}]")

        page += 1

    print(f"\nDEBUG: TOTAL LOC (cloc only): {total_loc}")
    return total_loc


def get_top_languages(limit=5):
    if not LOC_CACHE:
        return "Python | JavaScript | TypeScript | Rust | C++"

    language_totals = {}

    for _, langs in LOC_CACHE.items():
        for lang, loc in langs:
            language_totals[lang] = language_totals.get(lang, 0) + loc

    if not language_totals:
        return "Python | JavaScript | TypeScript | Rust | C++"

    sorted_langs = sorted(language_totals.items(), key=lambda x: x[1], reverse=True)
    top = sorted_langs[:limit]
    total = sum(language_totals.values())

    result = " | ".join(
        f"{lang} {round((loc / total) * 100)}%"
        for lang, loc in top
    )

    print(f"DEBUG: Top languages (cloc): {result}")
    return result


def graph_repos_stars(count_type, cursor=None, total_stars=0):
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
        request = simple_request('graph_repos_stars', query, variables)
        data = request.json()
        if cursor is None:  
            print(f"DEBUG: User {USER_NAME} repositories response received")
        
        if 'errors' in data:
            print(f"DEBUG: GraphQL errors: {data['errors']}")
            return 0
        
        user_data = data.get('data')
        if not user_data:
            return 0
        
        repos = user_data.get('user', {}).get('repositories', {})
        if not repos:
            return 0
        
        if count_type == 'repos':
            return repos.get('totalCount', 0)
        elif count_type == 'stars':
            edges = repos.get('edges', [])
            for edge in edges:
                node = edge.get('node')
                if node:
                    stars = node.get('stargazerCount', 0)
                    total_stars += stars
                    if stars > 0:
                        print(f"DEBUG: {node.get('name', 'unknown')} has {stars} stars")
            
            page_info = repos.get('pageInfo', {})
            if page_info.get('hasNextPage', False):
                return graph_repos_stars(count_type, page_info.get('endCursor'), total_stars)
            
            print(f"DEBUG: Total stars found: {total_stars}")
            return total_stars
    except Exception as e:
        print(f"DEBUG: Error in graph_repos_stars: {e}")
        import traceback
        traceback.print_exc()
        return 0
    
    return 0


def svg_overwrite(filename, age_data, commit_data, star_data, repo_data, loc_data, uptime_data, top_languages=''):
    if not os.path.exists(filename):
        return
    tree = etree.parse(filename)
    root = tree.getroot()
    
    find_and_replace(root, 'age_data', age_data)
    find_and_replace(root, 'commit_data', commit_data)
    find_and_replace(root, 'star_data', star_data)
    find_and_replace(root, 'repo_data', repo_data)
    find_and_replace(root, 'loc_data', loc_data)
    find_and_replace(root, 'uptime_data', uptime_data)
    if top_languages:
        find_and_replace(root, 'languages_data', top_languages)

    tree.write(filename, encoding='utf-8', xml_declaration=True)


def find_and_replace(root, element_id, new_text):
    if isinstance(new_text, int):
        new_text = f"{new_text:,}"
    element = root.find(f".//*[@id='{element_id}']")
    if element is not None:
        tspans = element.findall('.//{http://www.w3.org/2000/svg}tspan')
        if tspans:
            tspans[0].text = str(new_text)
        else:
            element.text = str(new_text)


def user_getter(username):
    query = 'query($login: String!){ user(login: $login) { id createdAt } }'
    try:
        request = simple_request('user_getter', query, {'login': username})
        data = request.json()
        
        if 'errors' in data:
            print(f"GraphQL Error in user_getter: {data['errors']}")
            return None, None
        
        user_data = data.get('data', {}).get('user')
        if not user_data:
            print("No user data returned from GraphQL")
            return None, None
        
        user_id = user_data.get('id')
        created_at = user_data.get('createdAt')
        
        if not user_id:
            print("User ID is None")
            return None, None
        
        return {'id': user_id}, created_at
    except Exception as e:
        print(f"Error in user_getter: {e}")
        import traceback
        traceback.print_exc()
        return None, None


def count_loc_with_cloc(repo_url, branch):
    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        repo_path = os.path.join(temp_dir, 'repo')
        
        auth_url = repo_url.replace('https://', f'https://{os.environ.get("ACCESS_TOKEN")}@')
        result = subprocess.run(
            ['git', 'clone', '--branch', branch, '--single-branch', '--depth', '1', auth_url, repo_path],
            capture_output=True, timeout=30, text=True
        )
        
        if result.returncode != 0:
            print(f"      [SKIPPED] Failed to clone repo")
            return 0, []
        
        result = subprocess.run(
            ['cloc', repo_path, '--json', '--exclude-dir=.git,.github,node_modules,vendor'],
            capture_output=True, timeout=90, text=True
        )
        
        if result.returncode == 0 and result.stdout:
            try:
                cloc_data = json.loads(result.stdout)
                lang_breakdown = []
                total_code = 0
                
                for lang_key, lang_data in cloc_data.items():
                    if lang_key not in ('SUM', 'header'):
                        if isinstance(lang_data, dict) and 'code' in lang_data:
                            code_lines = lang_data.get('code', 0)
                            if code_lines > 0 and lang_key in REAL_LANGUAGES:
                                lang_breakdown.append((lang_key, code_lines))
                                total_code += code_lines
                
                lang_breakdown.sort(key=lambda x: x[1], reverse=True)
                return int(total_code), lang_breakdown
            except (json.JSONDecodeError, ValueError):
                print(f"      [SKIPPED] Failed to parse cloc output")
                return 0, []
        else:
            print(f"      [SKIPPED] cloc failed")
            return 0, []
    except subprocess.TimeoutExpired:
        print(f"      [SKIPPED] Clone or cloc timeout")
        return 0, []
    except Exception as e:
        print(f"      [SKIPPED] Unexpected error: {e}")
        return 0, []
    finally:
        if temp_dir and os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == '__main__':
    env_type = "GitHub Actions" if IS_GITHUB_ACTIONS else "Local"
    print(f"DEBUG: Executando em: {env_type}")
    
    if not os.environ.get('ACCESS_TOKEN') or os.environ.get('ACCESS_TOKEN').strip() == '':
        print("ERRO: 'ACCESS_TOKEN' está vazio ou não configurado.")
        exit(1)
    
    if not os.environ.get('USER_NAME') or os.environ.get('USER_NAME').strip() == '':
        print("ERRO: 'USER_NAME' está vazio ou não configurado.")
        exit(1)
    
    print(f"DEBUG: Iniciando script com USER_NAME: {USER_NAME}")
    print(f"DEBUG: ACCESS_TOKEN primeiros 10 chars: {os.environ.get('ACCESS_TOKEN')[:10]}...")
    
    user_data, _ = user_getter(USER_NAME)
    
    if user_data is None:
        print("ERRO: Não foi possível obter dados do usuário. Verifique ACCESS_TOKEN e USER_NAME.")
        exit(1)
    
    age_str = daily_readme(BIRTHDAY)
    uptime_str = format_uptime(BIRTHDAY)
    stars = graph_repos_stars('stars')
    repos = graph_repos_stars('repos')
    commits = get_total_commits()
    loc_total = get_total_loc()
    top_langs = get_top_languages()

    print(f"DEBUG: Consultando usuário: {USER_NAME}")
    print(f"DEBUG: USER_ID: {user_data['id']}")
    
    svg_overwrite('dark_mode.svg', age_str, commits, stars, repos, loc_total, uptime_str, top_langs)
    svg_overwrite('light_mode.svg', age_str, commits, stars, repos, loc_total, uptime_str, top_langs)
    print("SVGs atualizados com sucesso.")